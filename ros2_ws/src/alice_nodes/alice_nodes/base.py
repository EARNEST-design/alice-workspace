"""Bounded participant lifecycle, immutable leases and independent watchdogs."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from alice_interfaces.msg import PlaybackStatus, RunHealth
from alice_interfaces.srv import BeginRun, EndRun
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

from alice.hardware.face_scope import face_manifest
from alice.hardware.manifest import load_manifest
from alice_nodes import contracts as wire
from alice_nodes.clock import clock_proof
from alice_nodes.transport import RunLifecycleGuard, SequenceGuard, StreamHeader

LATEST = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.BEST_EFFORT,
    durability=DurabilityPolicy.VOLATILE,
)
RELIABLE = QoSProfile(depth=128, reliability=ReliabilityPolicy.RELIABLE)
HEALTH = QoSProfile(depth=64, reliability=ReliabilityPolicy.RELIABLE)
LIMIT_NS = 250_000_000
SUCCESS_WORK_TIMEOUT_S = 1.0
SHUTDOWN_HANDLER_TIMEOUT_S = 5.0


def require_ros_hardware_visibility():
    # PID namespace visibility alone cannot prove access to every host FD table.
    # No trusted complete host verifier is qualified for this deployment. Never
    # infer absence of owners of either Maestro interface from empty fuser output.
    raise ValueError(
        "ROS hardware unavailable: complete host FD visibility for Maestro "
        "interfaces 00/02 is not qualified"
    )


@dataclass(frozen=True)
class RuntimePaths:
    config: Path
    hardware: Path
    output: Path
    fixtures: Path


def effective_files(root: Path, profile: str) -> dict[str, bytes]:
    if profile not in {"visible-face", "baseline"}:
        raise ValueError("unknown selected profile")
    files = {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix in {".json", ".yaml", ".jsonl"}
    }
    if profile == "visible-face":
        for target, source in [
            ("speech/sync-hardware-v1.json", "speech/sync-responsive-v1.json"),
            (
                "speech/authored-expression-v1.json",
                "speech/authored-expression-visible-v1.json",
            ),
            ("models/face-events-v1.yaml", "speech/face-events-full-blink-v1.yaml"),
        ]:
            files[target] = files[source]
    sync = json.loads(files["speech/sync-hardware-v1.json"])
    sync["max_duration_s"] = 10.0
    files["speech/sync-hardware-v1.json"] = json.dumps(sync, sort_keys=True).encode()
    files["speech/ros-runtime-limits-v1.json"] = json.dumps(
        {
            "schema_version": "ros-runtime-limits/v1",
            "sample_rate": 24000,
            "max_total_audio_s": 10,
            "max_transport_audio_s": 10 - sync["tail_s"],
            "tail_s": sync["tail_s"],
            "ring_s": 2,
            "startup_prebuffer_s": 0.2,
            "transport_chunk_s": 0.02,
            "mouth_lookahead_s": 0.1,
            "max_active_age_ms": 250,
            "health_period_ms": 50,
            "max_run_s": 25,
            "max_sad_hold_s": 2,
            "pose_deadline_s": 6,
        },
        sort_keys=True,
    ).encode()
    return files


def config_digest(root: Path, profile: str) -> str:
    files = effective_files(root, profile)
    return hashlib.sha256(
        json.dumps(
            {n: hashlib.sha256(v).hexdigest() for n, v in files.items()}, sort_keys=True
        ).encode()
    ).hexdigest()


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as out:
        json.dump(value, out, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)


@dataclass
class WorkItem:
    identity: object
    cancel: threading.Event
    operation: Callable
    finished: threading.Event = field(default_factory=threading.Event)
    retired: bool = False
    error: Exception | None = None
    latest: str | None = None


class RuntimeNode(Node):
    """All I/O hooks run outside run locks and the health callback group.

    A bounded worker owns inference; an independent finalizer owns terminal
    evidence. Every job captures its run identity and cancellation event. Evidence
    completion does not permit admission until all prior jobs have retired.
    The watchdog revokes capability even when inference stops making progress.
    """

    def __init__(self, role: str, *, paths: RuntimePaths | None = None):
        super().__init__(role, namespace="/alice")
        self.role = role
        self.incarnation = uuid.uuid4().hex
        defaults = paths or RuntimePaths(
            Path("/opt/alice/config"),
            Path("/opt/alice/hardware"),
            Path("/artifacts"),
            Path("/fixtures"),
        )
        for key, value in vars(defaults).items():
            self.declare_parameter(f"{key}_root", str(value))
        self.paths = RuntimePaths(
            **{k: Path(self.get_parameter(f"{k}_root").value) for k in vars(defaults)}
        )
        if not all(p.is_absolute() for p in vars(self.paths).values()):
            raise ValueError("runtime roots must be absolute installed paths")
        self.declare_parameter("hardware_enabled", False)
        self.declare_parameter("retain_raw", False)
        self.declare_parameter("image_identity", "unprovided")
        self.declare_parameter("code_identity", "unprovided")
        self.calibration = face_manifest(
            load_manifest(self.paths.hardware / "alice-face-v1.yaml")
        ).calibration_sha256
        self.identity = None
        self.lifecycle = RunLifecycleGuard()
        self.error = None
        self.state = RunHealth.PREPARING
        self.progress = 0
        self.peer_health = {}
        self.peers = {}
        self.stream_guards = {}
        self.sequences = {}
        self._lock = threading.RLock()
        self._lifecycle_busy = False
        self._ended = None
        self._completed = False
        self.artifact = ""
        self._seen_epochs = set()
        self._active_since = 0
        self._stop = threading.Event()
        self.cancel = threading.Event()
        self._outstanding = 0
        self._work_sealed = False
        self._work_context = threading.local()
        self._work_retired = threading.Condition(self._lock)
        self._final_outstanding = 0
        self._final_jobs = queue.Queue(maxsize=4)
        self._jobs = queue.Queue(maxsize=128)
        self._latest = {}
        self._worker = threading.Thread(
            target=self._work, name=f"{role}-worker", daemon=True
        )
        self._final_worker = threading.Thread(
            target=self._final_work, name=f"{role}-finalizer", daemon=True
        )
        self._watch = threading.Thread(
            target=self._watchdog, name=f"{role}-watchdog", daemon=True
        )
        self.group = ReentrantCallbackGroup()
        self.health_pub = self.create_publisher(RunHealth, "/alice/run/health", HEALTH)
        self.create_subscription(
            RunHealth,
            "/alice/run/health",
            self.receive_health,
            HEALTH,
            callback_group=self.group,
        )
        self.create_service(
            BeginRun,
            f"/alice/{role}/begin_run",
            self._begin_service,
            callback_group=self.group,
        )
        self.create_service(
            EndRun,
            f"/alice/{role}/end_run",
            self._end_service,
            callback_group=self.group,
        )
        self.create_timer(0.05, self.publish_health, callback_group=self.group)
        self._playing_since = None
        self._control_source = None
        self._control_drained = False
        if role in {"expression", "motion", "maestro"}:
            self.create_subscription(
                PlaybackStatus,
                "/alice/audio/playback_status",
                self.observe_playback,
                RELIABLE,
                callback_group=self.group,
            )
        self._final_worker.start()
        self._worker.start()
        self._watch.start()

    @property
    def binding(self):
        return self.lifecycle.binding

    def header(self, stream: str, source_ns: int | None = None) -> StreamHeader:
        with self._lock:
            sequence = self.sequences.get(stream, 0)
            self.sequences[stream] = sequence + 1
            return StreamHeader(
                self.identity,
                sequence,
                time.monotonic_ns() if source_ns is None else source_ns,
                self.incarnation,
            )

    def current(self, message) -> bool:
        # Inspect identity only. Old malformed bodies must never fault a new run.
        identity = message.header.identity
        return self.identity is not None and (
            identity.run_id,
            identity.epoch,
            identity.generation_id,
        ) == (self.identity.run_id, self.identity.epoch, self.identity.generation_id)

    def admit_header(self, header, producer, stream, *, exact=False, sparse=False):
        if header.identity != self.identity:
            return False
        item = getattr(self._work_context, "item", None)
        admitted_work = (
            item is not None
            and item.identity == self.identity
            and item.cancel is self.cancel
            and not item.retired
        )
        if (
            self.lifecycle.state != "active"
            or self.error
            or (
                self._ended is not None
                and not (self._ended == "success" and admitted_work)
            )
        ):
            return False
        if self.peers.get(producer) != header.publisher_incarnation:
            raise ValueError(
                f"{producer} publisher incarnation does not match prepared roster"
            )
        guard = self.stream_guards.setdefault(
            stream,
            SequenceGuard(
                self.identity, exact=exact, max_gap_ns=None if sparse else LIMIT_NS
            ),
        )
        return guard.admit(header, now_monotonic_ns=time.monotonic_ns())

    def submit(self, operation: Callable, *, latest: str | None = None):
        with self._lock:
            item = WorkItem(self.identity, self.cancel, operation, latest=latest)
            # Seal before coalescing: a later arrival cannot overwrite the last
            # valid item admitted before successful EndRun.
            if self.cancel.is_set() or self._work_sealed:
                item.retired = True
                item.finished.set()
                return item
            if latest is not None and latest in self._latest:
                item = self._latest[latest]
                item.operation = operation
                return item
            try:
                self._jobs.put_nowait(item)
                self._outstanding += 1
                if latest is not None:
                    self._latest[latest] = item
            except queue.Full:
                item.retired = True
                item.finished.set()
                self.fail("bounded worker queue exhausted")
            return item

    def call_worker(self, operation, timeout=45):
        item = self.submit(operation)
        if not item.finished.wait(timeout):
            with self._lock:
                item.retired = True
            raise RuntimeError("worker lifecycle deadline expired")
        if item.error:
            raise item.error
        if item.retired or item.cancel.is_set():
            raise RuntimeError("worker lifecycle cancelled")

    def _work(self):
        while not self._stop.is_set():
            try:
                item = self._jobs.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                with self._lock:
                    if item.latest:
                        self._latest.pop(item.latest, None)
                    valid = (
                        item.identity == self.identity
                        and item.cancel is self.cancel
                        and not item.cancel.is_set()
                        and not item.retired
                    )
                    if not valid:
                        item.retired = True
                if valid:
                    self._work_context.item = item
                    item.operation()
            except Exception as exc:
                item.error = exc
                if item.identity == self.identity:
                    self.fail(str(exc))
            finally:
                self._work_context.item = None
                with self._work_retired:
                    self._outstanding -= 1
                    item.finished.set()
                    self._work_retired.notify_all()

    def _queue_finalize(self, outcome):
        self._final_outstanding += 1
        self._final_jobs.put_nowait((self.identity, self.cancel, outcome))

    def _final_work(self):
        while not self._stop.is_set():
            try:
                identity, cancel, outcome = self._final_jobs.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                if identity == self.identity and cancel is self.cancel:
                    if outcome != "success" or self._await_success_work(
                        identity, cancel
                    ):
                        self._finalize(outcome)
            except Exception as exc:
                self.fail(str(exc))
            finally:
                with self._lock:
                    self._final_outstanding -= 1

    def _await_success_work(self, identity, cancel):
        deadline = time.monotonic() + SUCCESS_WORK_TIMEOUT_S
        with self._work_retired:
            while self._outstanding:
                if identity != self.identity or cancel.is_set():
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._work_retired.wait(remaining)
            else:
                return (
                    identity == self.identity and not cancel.is_set() and not self.error
                )
        # Fault cleanup remains independent of the blocked ordinary worker.
        self.fail("ordinary work did not retire before success deadline")
        return False

    def subscribe_work(self, message_type, topic, callback, *, latest=False, qos=None):
        def receive(message):
            if self.current(message):
                identity = self.identity
                self.submit(
                    lambda: (
                        callback(message)
                        if identity == self.identity and not self.error
                        else None
                    ),
                    latest=topic if latest else None,
                )

        return self.create_subscription(
            message_type,
            topic,
            receive,
            qos or (LATEST if latest else RELIABLE),
            callback_group=MutuallyExclusiveCallbackGroup(),
        )

    def _begin_service(self, request, response):
        return self.begin(request)

    def begin(self, request):
        owns_operation = False
        response = BeginRun.Response(
            identity=request.identity,
            responder_node_name=self.role,
            responder_incarnation=self.incarnation,
        )
        try:
            command = wire.validate_begin_run_request(request)
            binding = command.binding
            proof_ok = (
                clock_proof(binding.identity.epoch) == binding.clock_domain_fingerprint
            )
            if not proof_ok:
                raise ValueError("participant clock-domain mismatch")
            response.clock_verified = proof_ok
            if binding.hardware and not self.get_parameter("hardware_enabled").value:
                raise ValueError("deployment does not enable hardware")
            if binding.hardware:
                require_ros_hardware_visibility()
            if (
                binding.config_sha256
                != config_digest(self.paths.config, binding.selected_profile)
                or binding.calibration_sha256 != self.calibration
            ):
                raise ValueError("configuration/calibration identity mismatch")
            with self._lock:
                if self._lifecycle_busy:
                    raise ValueError("lifecycle operation in progress")
                if command.operation == "prepare":
                    if self.identity != binding.identity and self.identity is not None:
                        if (
                            not self._completed
                            or self._outstanding
                            or self._final_outstanding
                        ):
                            raise ValueError("prior run work has not retired")
                        if (
                            binding.identity.epoch in self._seen_epochs
                            or len(self._seen_epochs) >= 1024
                        ):
                            raise ValueError(
                                "epoch reused or process admission history full"
                            )
                        self.lifecycle = RunLifecycleGuard()
                    if self.identity == binding.identity and (
                        self.error or self._ended is not None
                    ):
                        raise ValueError("terminal run cannot be resurrected")
                    fresh = self.lifecycle.prepare(binding)
                    if fresh:
                        self.identity = binding.identity
                        self._seen_epochs.add(binding.identity.epoch)
                        self.error, self._ended, self._completed, self.artifact = (
                            None,
                            None,
                            False,
                            "",
                        )
                        (
                            self.stream_guards,
                            self.sequences,
                            self.peer_health,
                            self.peers,
                        ) = {}, {}, {}, {}
                        self.cancel = threading.Event()
                        self._work_sealed = False
                        self._playing_since = None
                        self._control_source = None
                        self._control_drained = False
                        self.progress = 0
                        self._active_since = 0
                        self.state = RunHealth.PREPARING
                else:
                    if self.error or self._ended is not None:
                        raise ValueError("terminal run cannot start")
                    roster = {p.node_name: p.incarnation for p in binding.peers}
                    if (
                        roster.get(self.role) != self.incarnation
                        or roster.get("session") != binding.requester_incarnation
                    ):
                        raise ValueError("start roster incarnation mismatch")
                    fresh = self.lifecycle.start(binding)
                    self.peers = roster
                    if fresh:
                        self._active_since = time.monotonic_ns()
                self._lifecycle_busy = fresh
                owns_operation = fresh
            if fresh:
                if command.operation == "prepare":
                    self.run_dir = (
                        self.paths.output
                        / hashlib.sha256(binding.identity.epoch.encode()).hexdigest()[
                            :24
                        ]
                    )
                    self.local_dir = self.run_dir / self.role
                    self.local_dir.mkdir(parents=True, exist_ok=False)
                    self.config_root = self.local_dir / "config"
                    for name, payload in effective_files(
                        self.paths.config, binding.selected_profile
                    ).items():
                        target = self.config_root / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(payload)
                    self.call_worker(self.prepare_run)
                    if self.error:
                        raise RuntimeError(self.error)
                    self.state = RunHealth.READY
                else:
                    self.call_worker(self.start_run, timeout=5)
                    if self.error:
                        raise RuntimeError(self.error)
                    self.state = RunHealth.ACTIVE
            if self.error:
                raise RuntimeError(self.error)
            response.accepted = True
            response.idempotent = not fresh
            response.lifecycle_state = (
                response.PREPARED if command.operation == "prepare" else response.ACTIVE
            )
        except Exception as exc:
            response.error = str(exc)[:256] or "begin rejected"
            if owns_operation:
                self.fail(response.error)
        finally:
            if owns_operation:
                self._lifecycle_busy = False
        return response

    def prepare_run(self):
        pass

    def start_run(self):
        pass

    def _end_service(self, request, response):
        return self.end(request)

    def end(self, request):
        response = EndRun.Response(
            identity=request.identity,
            responder_incarnation=self.incarnation,
            terminal_outcome=request.outcome,
        )
        try:
            command = wire.validate_end_run_request(request)
            if (
                command.identity != self.identity
                or command.requester_incarnation != self.binding.requester_incarnation
            ):
                raise ValueError("end run identity/requester mismatch")
            if self.error and command.outcome == "success":
                raise ValueError("fault cannot become success")
            with self._lock:
                if (
                    self._ended == "success"
                    and self.error
                    and command.outcome != "success"
                    and not self._completed
                ):
                    self._ended = None
                if self._ended is not None and self._ended != command.outcome:
                    raise ValueError("conflicting terminal outcome")
                response.idempotent = self._ended is not None
                if self._ended is None:
                    self.validate_end(command.outcome)
                    self._ended = command.outcome
                    self._work_sealed = True
                    if command.outcome != "success":
                        self.fail(command.reason)
                    else:
                        self.state = RunHealth.FINALIZING
                    if command.outcome == "success":
                        self._queue_finalize(command.outcome)
                response.accepted = True
                response.completed = self._completed
                response.lifecycle_state = (
                    response.STATE_COMPLETED
                    if self._completed
                    else response.STATE_FINALIZING
                )
                response.artifact_identity = self.artifact
        except Exception as exc:
            response.error = str(exc)[:256] or "end rejected"
        return response

    def validate_end(self, outcome):
        pass

    def _finalize(self, outcome):
        cleanup_error = None
        try:
            self.finalize_run(outcome)
        except Exception as exc:
            if outcome == "success":
                raise
            cleanup_error = str(exc)
        if outcome == "success" and self.error:
            return
        files = {
            str(p.relative_to(self.local_dir)): hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
            for p in sorted(self.local_dir.rglob("*"))
            if p.is_file() and p.name != "terminal.json" and p.suffix != ".tmp"
        }
        evidence = {
            "files": files,
            "node": self.role,
            "incarnation": self.incarnation,
            "identity": vars(self.identity),
            "outcome": outcome,
            "error": self.error,
            "cleanup_error": cleanup_error,
            "config_sha256": self.binding.config_sha256,
            "calibration_sha256": self.calibration,
            "clock_verified": True,
            "hardware": self.binding.hardware,
            "seed": self.binding.seed,
            "image_identity": self.get_parameter("image_identity").value,
            "code_identity": self.get_parameter("code_identity").value,
            "raw_retention": self.get_parameter("retain_raw").value,
        }
        write_json(self.local_dir / "terminal.json", evidence)
        self.artifact = hashlib.sha256(
            (self.local_dir / "terminal.json").read_bytes()
        ).hexdigest()
        self._completed = True
        if not self.error:
            self.state = RunHealth.COMPLETED

    def finalize_run(self, outcome):
        pass

    def fail(self, detail):
        with self._lock:
            if self.error is not None or self._completed:
                return
            self.error = str(detail)[:256] or "runtime failure"
            self.state = RunHealth.FAULT
            self.cancel.set()
            self._work_sealed = True
            self._work_retired.notify_all()
            # Retire queued jobs before terminal evidence can admit another epoch.
            while True:
                try:
                    item = self._jobs.get_nowait()
                except queue.Empty:
                    break
                item.retired = True
                item.finished.set()
                self._outstanding -= 1
            self._latest.clear()
            terminal = hasattr(self, "local_dir") and not self._final_outstanding
            if hasattr(self, "local_dir") and self._ended in (None, "success"):
                self._ended = "fault"
                terminal = True
        self.stop_local()
        if terminal:
            with self._lock:
                self._queue_finalize(self._ended or "fault")

    def stop_local(self):
        """Only immediate local signals; blocking cleanup belongs to workers."""

    def publish_health(self):
        if self.identity is not None:
            self.health_pub.publish(
                RunHealth(
                    header=wire.stream_header_to_msg(self.header("health")),
                    schema_version="run-health/v1",
                    state=self.state,
                    progress=self.progress,
                    detail=self.error or "",
                )
            )

    def receive_health(self, message):
        if not self.current(message) or not self.peers:
            return
        try:
            header = wire.stream_header_from_msg(message.header)
            names = [
                n for n, i in self.peers.items() if i == header.publisher_incarnation
            ]
            if not names:
                raise ValueError("health publisher incarnation not in roster")
            name = names[0]
            if name == self.role:
                return
            # Terminal states are still leased during asynchronous finalization.
            guard = self.stream_guards.setdefault(
                "health:" + name, SequenceGuard(self.identity, exact=False)
            )
            if not guard.admit(header, now_monotonic_ns=time.monotonic_ns()):
                return
            health = wire.validate_run_health(message)
            self.peer_health[name] = (header.source_monotonic_ns, health)
            if health.state in {"fault", "cancelled"}:
                self.fail(f"{name}: {health.detail}")
        except Exception as exc:
            self.fail(str(exc))

    def _watchdog(self):
        while not self._stop.wait(0.02):
            if (
                self.identity is None
                or self.error
                or self._completed
                or not self._active_since
            ):
                continue
            now = time.monotonic_ns()
            if now - self._active_since > 25_000_000_000:
                self.fail("run duration exceeded 25 seconds")
                continue
            if now - self._active_since > LIMIT_NS:
                for peer in self.peers.keys() - {self.role}:
                    if (
                        now - self.peer_health.get(peer, (self._active_since, None))[0]
                        > LIMIT_NS
                    ):
                        self.fail(f"{peer} health lease expired")
                        break
            try:
                self.check_progress(now)
            except Exception as exc:
                self.fail(str(exc))

    def observe_playback(self, message):
        # Independent of inference: first progress begins at actual DAC playback.
        if not self.current(message) or not self.peers or self.error:
            return
        try:
            header = wire.stream_header_from_msg(message.header)
            if not self.admit_header(header, "audio", "control-playback"):
                return
            value = wire.validate_playback_status(message)
            if value.state == "playing" and self._playing_since is None:
                self._playing_since = header.source_monotonic_ns
            if value.drained:
                # Drain cannot forgive a stream that never delivered control.
                self.check_progress(time.monotonic_ns())
                if self._control_source is None:
                    raise RuntimeError("first control source progress missing at drain")
                self._control_drained = True
            if value.state in {"fault", "cancelled"}:
                self.fail(value.error or "audio cancelled")
        except Exception as exc:
            self.fail(str(exc))

    def check_progress(self, now):
        if self._playing_since is not None and not self._control_drained:
            source = (
                self._control_source
                if self._control_source is not None
                else self._playing_since
            )
            if now - source > LIMIT_NS:
                raise RuntimeError("original control source progress expired")

    def destroy_node(self):
        if self.identity is not None and not self._completed:
            self.fail("node shutdown")
            deadline = time.monotonic() + 2
            while not self._completed and time.monotonic() < deadline:
                time.sleep(0.01)
        self._stop.set()
        self._worker.join(timeout=2)
        self._watch.join(timeout=1)
        self._final_worker.join(timeout=2)
        return super().destroy_node()


def spin(factory, args=None):
    import signal

    import rclpy
    from rclpy.signals import SignalHandlerOptions

    # rclpy's default SIGTERM handler destroys the context before application
    # cleanup. Keep it live for local revocation/evidence and executor retirement.
    stop = threading.Event()
    previous = {
        signum: signal.signal(signum, lambda *_: stop.set())
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    node = factory()
    executor = MultiThreadedExecutor(num_threads=6)
    executor.add_node(node)
    try:
        while not stop.is_set() and rclpy.ok():
            executor.spin_once(timeout_sec=0.05)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        try:
            if node.identity is not None and not node._completed:
                node.fail("node shutdown")
            # Lyrical rclpy destroys its wake guard before joining its pool.
            # Already queued handlers still trigger that guard on entry. Spin
            # scheduling is stopped above; retire those bounded handlers first.
            # Keep this compatibility ordering covered by the saturated SIGTERM
            # regression until upstream shutdown joins before guard destruction.
            pool = executor._executor
            pool.shutdown(wait=False)
            deadline = time.monotonic() + SHUTDOWN_HANDLER_TIMEOUT_S
            threads = tuple(pool._threads)
            for thread in threads:
                thread.join(timeout=max(0, deadline - time.monotonic()))
            if any(thread.is_alive() for thread in threads):
                # Python cannot interrupt a stuck callback safely. Local fail()
                # above already revoked actuation and launched independent fault
                # evidence. Do not destroy entities or wait in Python's atexit
                # pool join; make this failed process boundary explicit instead.
                reason = "ROS handler quiescence deadline expired during shutdown"
                try:
                    node.paths.output.mkdir(parents=True, exist_ok=True)
                    write_json(
                        node.paths.output
                        / f"shutdown-{node.role}-{node.incarnation}.json",
                        {
                            "node": node.role,
                            "quiescent": False,
                            "deadline_seconds": SHUTDOWN_HANDLER_TIMEOUT_S,
                            "error": reason,
                            "local_terminal_completed": node._completed,
                            "cancel_requested": node.cancel.is_set(),
                        },
                    )
                finally:
                    # The artifact and exit status surface the failure. Stderr
                    # can be closed or blocked; never let diagnostic I/O prevent
                    # the process boundary after the drain deadline.
                    os._exit(1)
            executor.shutdown(timeout_sec=5)
            node.destroy_node()
        finally:
            rclpy.try_shutdown()
            for signum, handler in previous.items():
                signal.signal(signum, handler)
