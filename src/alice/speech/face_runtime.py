"""Serial-owner thread and independent face watchdog; no devices by default."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Event, Lock, Thread

from alice.contracts.actuation import PoseRequest
from alice.contracts.motion import TargetUpdate
from alice.hardware.maestro_adapter import (
    MaestroPreflightSnapshot,
    MaestroStreamReceipt,
)
from alice.hardware.manifest import HardwareManifest
from alice.speech.face_stream import FaceCommandStream


class SimulatedFaceDriver:
    """Immediate simulated PWM observations, explicitly never physical evidence."""

    def __init__(self, manifest: HardwareManifest) -> None:
        self.manifest = manifest
        self.positions = {a.name: a.home_qus for a in manifest.actuators}
        self.closed = False

    def stream_face_target(
        self, token: str, request: PoseRequest
    ) -> MaestroStreamReceipt:
        if self.closed:
            raise RuntimeError("simulated face is closed")
        target = request.targets[0]
        qus = self.manifest.actuator(target.actuator_name).target_qus(
            target.normalized_position
        )
        now = time.monotonic_ns()
        self.positions[target.actuator_name] = qus
        return MaestroStreamReceipt(
            target_qus=qus,
            observed_qus=qus,
            sent_monotonic_ns=now,
            reported_monotonic_ns=now,
        )

    def read_only_preflight(self, names: tuple[str, ...]) -> MaestroPreflightSnapshot:
        if self.closed:
            raise RuntimeError("simulated face is closed")
        return MaestroPreflightSnapshot(
            controller_error_register=0,
            positions_qus={n: self.positions[n] for n in names},
            observed_monotonic_ns=time.monotonic_ns(),
        )

    def restore_jaw_response(self) -> None:
        if self.closed or any(
            self.positions[a.name] != a.home_qus for a in self.manifest.actuators
        ):
            raise RuntimeError("simulated restoration requires Home")

    def close(self) -> None:
        self.closed = True


class FaceRuntime:
    def __init__(
        self,
        factory: Callable[[Event], FaceCommandStream],
        *,
        on_fault: Callable[[], None],
    ) -> None:
        self.factory, self.on_fault = factory, on_fault
        self.stream: FaceCommandStream | None = None
        self.ready, self.done = Event(), Event()
        self._stop, self._success, self._has_source = Event(), Event(), Event()
        self._error_lock = Lock()
        self._hold_pose: TargetUpdate | None = None
        self._hold_s = 0.0
        self.error: BaseException | None = None
        self._thread = Thread(target=self._run, name="face-serial-owner", daemon=True)
        self._watchdog = Thread(target=self._watch, name="face-watchdog", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._watchdog.start()

    @property
    def cancel_signal(self) -> Event:
        """Callback-safe revocation signal shared with the serial adapter."""
        return self._stop

    def abort(self, detail: str) -> None:
        if self.done.is_set():
            return
        with self._error_lock:
            if self.error is None:
                self.error = RuntimeError(detail)
        self._stop.set()
        # PCM abort occurs before any serial cleanup can block.
        self.on_fault()
        if self.stream is not None:
            try:
                self.stream.revoke(detail)
            except BaseException as exc:
                assert self.error is not None
                self.error.add_note(f"face close: {exc}")

    def offer(
        self,
        proposal: TargetUpdate,
        generation_id: str,
        sample: int,
        *,
        source_monotonic_ns: int | None = None,
    ) -> None:
        self.raise_if_failed()
        if self.stream is None or not self.ready.is_set():
            raise RuntimeError("face runtime not ready")
        self.stream.offer(
            proposal, generation_id, sample, source_monotonic_ns=source_monotonic_ns
        )
        self._has_source.set()

    def complete(
        self,
        *,
        hold_pose: TargetUpdate | None = None,
        hold_s: float = 0,
    ) -> None:
        """Called by the coordinator only after successful audio completion."""
        self.raise_if_failed()
        if not 0 <= hold_s <= 2 or (hold_s > 0) != (hold_pose is not None):
            raise ValueError("post-speech pose requires a hold in (0, 2] seconds")
        if self.stream is None or self._success.is_set() or self.done.is_set():
            raise RuntimeError("face completion is not available")
        if hold_pose is not None:
            self.stream.pose_positions(hold_pose)
        self._hold_pose, self._hold_s = hold_pose, hold_s
        self._success.set()

    def raise_if_failed(self) -> None:
        if self.error is not None:
            raise RuntimeError(f"face execution failed: {self.error}") from self.error

    def join(self) -> None:
        self._thread.join(timeout=2)
        self._watchdog.join(timeout=1)
        if self._thread.is_alive() or self._watchdog.is_alive():
            raise RuntimeError("face owner/watchdog failed to stop")

    def _watch(self) -> None:
        while not self.done.wait(0.02):
            stream = self.stream
            if (
                stream is not None
                and time.monotonic_ns() - stream.last_progress_ns > 250_000_000
            ):
                self.abort("face serial watchdog expired")
                return

    def _run(self) -> None:
        try:
            self.stream = self.factory(self._stop)
            if self._stop.is_set():
                self.stream.revoke("cancelled during face startup")
                return
            self.ready.set()
            while not self._stop.is_set() and not self._success.is_set():
                self.stream.step(waiting=not self._has_source.is_set())
                self._stop.wait(0.002)
            if self._stop.is_set():
                return
            if self._hold_pose is not None:
                deadline = time.monotonic() + 6
                settled: float | None = None
                while not self._stop.is_set():
                    if time.monotonic() > deadline:
                        raise RuntimeError("post-speech pose exceeded deadline")
                    self.stream.step(hold_pose=self._hold_pose)
                    if self.stream.at_pose(self._hold_pose):
                        if settled is None:
                            settled = time.monotonic()
                        if time.monotonic() - settled >= self._hold_s:
                            break
                    else:
                        settled = None
                    self._stop.wait(0.002)
                if self._stop.is_set():
                    return
            deadline = time.monotonic() + 6
            while not self.stream.at_home:
                if self._stop.is_set():
                    return
                if time.monotonic() > deadline:
                    raise RuntimeError("face Home ramp exceeded deadline")
                self.stream.step(home=True)
                self._stop.wait(0.002)
            self.stream.finish()
        except BaseException as exc:
            with self._error_lock:
                if self.error is None:
                    self.error = exc
            self.abort(str(exc))
        finally:
            if self.stream is not None:
                try:
                    self.stream.driver.close()
                except BaseException as exc:
                    self.abort(f"face close failed: {exc}")
            self.ready.set()
            self.done.set()
