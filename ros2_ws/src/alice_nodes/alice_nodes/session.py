"""Explicit action admission, prepare/start barrier and terminal evidence barrier."""

import queue
import threading
import time
import uuid

from alice_interfaces.action import RunSpeech
from alice_interfaces.msg import PeerIdentity, PlaybackStatus, ServoReceipt
from alice_interfaces.msg import SpeechClause as ClauseMsg
from alice_interfaces.srv import BeginRun, EndRun
from rclpy.action import ActionServer, CancelResponse, GoalResponse

from alice.contracts.speech_stream import ClauseSequence, SpeechClause
from alice_nodes import contracts as wire
from alice_nodes.base import RELIABLE, RuntimeNode, clock_proof, spin
from alice_nodes.transport import RUNTIME_NODE_NAMES, RunIdentity, SequenceGuard
from alice_nodes.transport import PeerIdentity as PeerValue


class SessionNode(RuntimeNode):
    def __init__(self, **kwargs):
        super().__init__("session", **kwargs)
        self._admission = threading.Lock()
        self.action = ActionServer(
            self,
            RunSpeech,
            "/alice/run_speech",
            execute_callback=self.execute,
            goal_callback=self.goal,
            cancel_callback=self.cancel_goal,
            callback_group=self.group,
        )
        self.peer_clients = {
            n: (
                self.create_client(
                    BeginRun, f"/alice/{n}/begin_run", callback_group=self.group
                ),
                self.create_client(
                    EndRun, f"/alice/{n}/end_run", callback_group=self.group
                ),
            )
            for n in RUNTIME_NODE_NAMES - {"session"}
        }
        self.publisher = self.create_publisher(
            ClauseMsg, "/alice/speech/clauses", RELIABLE
        )
        self.create_subscription(
            PlaybackStatus,
            "/alice/audio/playback_status",
            self.playback,
            RELIABLE,
            callback_group=self.group,
        )
        self.create_subscription(
            ServoReceipt,
            "/alice/maestro/servo_receipt",
            self.receipt,
            RELIABLE,
            callback_group=self.group,
        )
        self.create_subscription(
            ClauseMsg,
            "/alice/run/committed_clauses",
            self.external_clause,
            RELIABLE,
            callback_group=self.group,
        )
        self.playback_value = None
        self.external = queue.Queue(maxsize=32)

    def goal(self, goal):
        try:
            wire.validate_run_speech_goal(goal)
            if not self._admission.acquire(blocking=False):
                return GoalResponse.REJECT
            return GoalResponse.ACCEPT
        except Exception:
            return GoalResponse.REJECT

    def cancel_goal(self, handle):
        self.fail("action cancelled")
        return CancelResponse.ACCEPT

    def prepare_run(self):
        self.playback_value = None
        self.committed = self.receipts = 0
        self.external = queue.Queue(maxsize=32)
        self.external_guard = SequenceGuard(self.identity, exact=True, max_gap_ns=None)
        self.external_ledger = ClauseSequence()

    def playback(self, message):
        if not self.current(message) or not self.peers:
            return
        try:
            header = wire.stream_header_from_msg(message.header)
            if self.admit_header(header, "audio", "playback"):
                self.playback_value = wire.validate_playback_status(message)
                if self.playback_value.state in {"fault", "cancelled"}:
                    self.fail(self.playback_value.error or "audio cancelled")
        except Exception as exc:
            self.fail(str(exc))

    def receipt(self, message):
        if not self.current(message) or not self.peers:
            return
        try:
            header = wire.stream_header_from_msg(message.header)
            if self.admit_header(header, "maestro", "receipt", sparse=True):
                wire.validate_servo_receipt(
                    message, expected_calibration_sha256=self.calibration
                )
                self.receipts += 1
        except Exception as exc:
            self.fail(str(exc))

    def external_clause(self, message):
        if not self.current(message) or not getattr(self, "accept_external", False):
            return
        try:
            header = wire.stream_header_from_msg(message.header)
            if header.publisher_incarnation != self.external_incarnation:
                raise ValueError("external source incarnation mismatch")
            if not self.external_guard.admit(
                header, now_monotonic_ns=time.monotonic_ns()
            ):
                return
            clause = wire.speech_clause_from_msg(message)
            self.external_ledger.commit(clause)
            self.external.put_nowait((header, clause))
        except Exception as exc:
            self.fail(str(exc))

    def relay_external(self, queued, *, now_ns=None):
        header, clause = queued
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        if (
            header.identity != self.identity
            or not 0 <= now_ns - header.source_monotonic_ns <= 250_000_000
        ):
            raise ValueError("external clause original source expired at relay")
        return wire.speech_clause_to_msg(
            clause, self.header("clauses", header.source_monotonic_ns)
        )

    def rpc(self, name, request, *, timeout=5):
        if name == "session":
            return (
                self.begin(request)
                if isinstance(request, BeginRun.Request)
                else self.end(request)
            )
        client = self.peer_clients[name][
            0 if isinstance(request, BeginRun.Request) else 1
        ]
        if not client.wait_for_service(timeout_sec=min(timeout, 5)):
            raise RuntimeError(f"{name} lifecycle service unavailable")
        future = client.call_async(request)
        deadline = time.monotonic() + timeout
        while not future.done():
            if time.monotonic() > deadline:
                raise RuntimeError(f"{name} lifecycle deadline expired")
            time.sleep(0.005)
        return future.result()

    def feedback(self, handle):
        playback = self.playback_value
        submitted = playback.submitted_samples if playback else 0
        played = playback.played_samples if playback else 0
        # Audio health carries total generated output including its local tail.
        # A newer playback receipt also proves at least that many generated samples.
        audio_health = self.peer_health.get("audio")
        generated = max(submitted, audio_health[1].progress if audio_health else 0)
        handle.publish_feedback(
            RunSpeech.Feedback(
                header=wire.stream_header_to_msg(self.header("feedback")),
                committed_clauses=self.committed,
                generated_samples=generated,
                submitted_samples=submitted,
                played_samples=played,
                playback_state=RunSpeech.Feedback.DRAINED
                if playback and playback.drained
                else RunSpeech.Feedback.PLAYING
                if playback and playback.state == "playing"
                else RunSpeech.Feedback.BUFFERING,
            )
        )

    def execute(self, handle):
        prepared = []
        # execute is entered only after goal() validates and admits the action.
        # Admission survives later participant preparation/execution failures.
        result = RunSpeech.Result(accepted=True, responder_incarnation=self.incarnation)
        try:
            goal = wire.validate_run_speech_goal(handle.request)
            # Repeated fixture actions also receive a fresh authoritative epoch.
            identity = RunIdentity(
                goal.identity.run_id, uuid.uuid4().hex, goal.identity.generation_id
            )
            result.identity = wire.run_identity_to_msg(identity)
            request = BeginRun.Request(
                schema_version="begin-run/v1",
                identity=result.identity,
                operation=BeginRun.Request.PREPARE,
                selected_profile=goal.selected_profile,
                seed=goal.seed,
                hardware=goal.hardware,
                sad_hold_ms=goal.sad_hold_ms,
                config_sha256=goal.config_sha256,
                calibration_sha256=goal.calibration_sha256,
                clock_domain_fingerprint=clock_proof(identity.epoch),
                requester_incarnation=self.incarnation,
            )
            roster = []
            # Warm TTS before activation, while hardware remains idle.
            for name in [
                "session",
                "tts",
                "audio",
                "expression",
                "motion",
                "maestro",
                "perception",
                "recorder",
            ]:
                reply = self.rpc(name, request, timeout=45)
                ack = wire.validate_begin_run_response(
                    reply,
                    expected_identity=identity,
                    expected_responder=PeerValue(name, reply.responder_incarnation),
                    requested_operation="prepare",
                    hardware=goal.hardware,
                )
                if not ack.accepted:
                    raise RuntimeError(f"{name} prepare: {ack.error}")
                prepared.append(name)
                roster.append(
                    PeerIdentity(node_name=name, incarnation=ack.responder.incarnation)
                )
                if handle.is_cancel_requested:
                    raise InterruptedError("action cancelled during preparation")
            request.operation = BeginRun.Request.START
            request.peers = roster
            for name in [
                "session",
                "recorder",
                "audio",
                "expression",
                "motion",
                "perception",
                "tts",
                "maestro",
            ]:
                if self.error or handle.is_cancel_requested:
                    raise RuntimeError(self.error or "action cancelled")
                reply = self.rpc(name, request)
                ack = wire.validate_begin_run_response(
                    reply,
                    expected_identity=identity,
                    expected_responder=PeerValue(
                        name, dict((p.node_name, p.incarnation) for p in roster)[name]
                    ),
                    requested_operation="start",
                    hardware=goal.hardware,
                )
                if not ack.accepted:
                    raise RuntimeError(f"{name} start: {ack.error}")
            self.external_incarnation = goal.requester_incarnation
            self.accept_external = goal.source == "committed_clauses"
            self.feedback(
                handle
            )  # exposes authoritative epoch before external source admission
            ledger = ClauseSequence()
            if goal.source == "fixture":
                path = self.paths.fixtures / goal.fixture_name
                if (
                    path.is_symlink()
                    or not path.resolve().is_relative_to(self.paths.fixtures.resolve())
                    or path.stat().st_size > 100_000
                ):
                    raise ValueError("fixture exceeds trusted source boundary")
                with path.open() as source:
                    for line in source:
                        clause = SpeechClause.model_validate_json(line)
                        if clause.generation_id != identity.generation_id:
                            raise ValueError(
                                "fixture generation differs from admitted goal"
                            )
                        if self.error or handle.is_cancel_requested:
                            raise RuntimeError(self.error or "action cancelled")
                        ledger.commit(clause)
                        self.publisher.publish(
                            wire.speech_clause_to_msg(clause, self.header("clauses"))
                        )
                        self.committed += 1
                ledger.finish()
            else:
                deadline = time.monotonic() + 10
                while True:
                    if self.error or handle.is_cancel_requested:
                        raise RuntimeError(self.error or "action cancelled")
                    if time.monotonic() > deadline:
                        raise RuntimeError("external source deadline expired")
                    try:
                        queued = self.external.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    message = self.relay_external(queued)
                    clause = queued[1]
                    ledger.commit(clause)
                    self.publisher.publish(message)
                    self.committed += 1
                    if clause.end_of_response:
                        ledger.finish()
                        break
            deadline = time.monotonic() + 15
            while not (self.playback_value and self.playback_value.drained):
                if self.error or handle.is_cancel_requested:
                    raise RuntimeError(self.error or "action cancelled")
                if time.monotonic() > deadline:
                    raise RuntimeError("audio completion deadline expired")
                self.feedback(handle)
                time.sleep(0.05)
            # End acceptance is not completion. Poll Maestro through Home and flush,
            # then other local evidence, and recorder last.
            for name in [
                "maestro",
                "audio",
                "tts",
                "expression",
                "motion",
                "perception",
                "recorder",
                "session",
            ]:
                self.finish_peer(name, EndRun.Request.SUCCESS, "completed", handle)
            result.terminal_outcome = RunSpeech.Result.SUCCESS
            result.artifact_identity = self.recorder_artifact
            handle.succeed()
        except Exception as exc:
            cancelled = handle.is_cancel_requested
            self.fail(str(exc))
            result.error = str(exc)[:256] or "session failed"
            result.terminal_outcome = (
                RunSpeech.Result.CANCELLED if cancelled else RunSpeech.Result.FAULT
            )
            for name in [n for n in prepared if n != "recorder"] + (
                ["recorder"] if "recorder" in prepared else []
            ):
                try:
                    self.finish_peer(
                        name,
                        EndRun.Request.CANCELLED if cancelled else EndRun.Request.FAULT,
                        result.error,
                        None,
                    )
                except Exception:
                    pass
            handle.canceled() if cancelled else handle.abort()
        finally:
            self.accept_external = False
            self._admission.release()
        return result

    def finish_peer(self, name, outcome, reason, handle):
        request = EndRun.Request(
            schema_version="end-run/v1",
            identity=wire.run_identity_to_msg(self.identity),
            outcome=outcome,
            reason=reason,
            requester_incarnation=self.incarnation,
        )
        deadline = time.monotonic() + (14 if name == "maestro" else 5)
        while time.monotonic() < deadline:
            reply = self.rpc(name, request)
            ack = wire.validate_end_run_response(reply, expected_identity=self.identity)
            if (
                ack.responder_incarnation
                != self.peers.get(
                    name,
                    self.incarnation
                    if name == "session"
                    else reply.responder_incarnation,
                )
                or ack.terminal_outcome
                != {0: "success", 1: "cancelled", 2: "fault"}[outcome]
            ):
                raise RuntimeError("end response incarnation/outcome mismatch")
            if not ack.accepted:
                raise RuntimeError(f"{name} finalization: {ack.error}")
            if ack.completed:
                if name == "recorder":
                    self.recorder_artifact = ack.artifact_identity
                return
            if outcome == EndRun.Request.SUCCESS and self.error:
                raise RuntimeError(self.error)
            if handle:
                self.feedback(handle)
            time.sleep(0.05)
        raise RuntimeError(f"{name} finalization deadline expired")


def create_node(**kwargs):
    return SessionNode(**kwargs)


def main():
    spin(create_node)
