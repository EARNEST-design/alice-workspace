"""Trusted selected-face command stream with a coalescing proposal mailbox."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock
from typing import Protocol

from alice.contracts.actuation import ActuatorTarget, PoseRequest
from alice.contracts.motion import TargetUpdate
from alice.hardware.face_scope import FACE_CHANNELS, face_manifest, face_profiles
from alice.hardware.maestro_adapter import (
    MaestroPreflightSnapshot,
    MaestroStreamReceipt,
)
from alice.hardware.maestro_face import FaceTransactionError
from alice.hardware.manifest import ActuatorDefinition, HardwareManifest
from alice.safety.supervisor import RunState, SafetySupervisor
from alice.speech.jaw_trial import JawTrialConfig, next_jaw_target


class FaceDriver(Protocol):
    def stream_face_target(
        self, token: str, request: PoseRequest
    ) -> MaestroStreamReceipt: ...
    def read_only_preflight(
        self, names: tuple[str, ...]
    ) -> MaestroPreflightSnapshot: ...
    def restore_jaw_response(self) -> None: ...
    def close(self) -> None: ...


@dataclass
class ChannelState:
    position: float
    velocity: float
    sent_ns: int


def normalized_qus(a: ActuatorDefinition, qus: int) -> float:
    span = (
        a.home_qus - a.software_min_qus
        if qus < a.home_qus
        else a.software_max_qus - a.home_qus
    )
    return (qus - a.home_qus) / span


def bounded_quantized_target(
    state: ChannelState,
    desired: float,
    dt: float,
    profile: JawTrialConfig,
    definition: ActuatorDefinition,
) -> float:
    planned = next_jaw_target(
        state.position,
        state.velocity,
        desired,
        dt,
        profile,
        # A slow response can round back to the same pulse indefinitely. Snap
        # within one response horizon's pulse resolution, still inside every
        # derivative bound below (including dispatch uncertainty).
        endpoint_tolerance=profile.response_time_s
        / profile.command_interval_s
        / min(
            definition.home_qus - definition.software_min_qus,
            definition.software_max_qus - definition.home_qus,
        ),
    )
    pulse = definition.target_qus(planned)
    # Quantization is inside the independent limits. Check both extrema and the
    # stationary points of v*t +/- a*t^2 throughout the dispatch time budget.
    times = [dt, dt + 0.002]
    a = profile.max_acceleration_per_s2
    for t in (state.velocity / (2 * a), -state.velocity / (2 * a)):
        if dt < t < dt + 0.002:
            times.append(t)
    candidates = [normalized_qus(definition, q) for q in range(pulse - 3, pulse + 4)]
    valid = [
        p
        for p in candidates
        if (
            profile.closed_position <= p <= profile.open_position
            and abs(p - state.position) <= profile.max_step + 1e-12
            and all(
                abs((p - state.position) / t) <= profile.max_rate_per_s + 1e-12
                and abs((p - state.position) / t - state.velocity) / t <= a + 1e-12
                for t in times
            )
        )
    ]
    if not valid:
        raise RuntimeError("no quantized face target within command limits")
    return min(valid, key=lambda p: abs(p - planned))


class FaceCommandStream:
    """One owner calls step/finish. Producers only replace the latest pose.

    Each channel is planned just before its write, after preceding transactions.
    State is advanced from integer sent targets and write-start time, never from
    the later PWM readback or from speculative proposals. Revocation closes with
    no recovery writes and may be called by a watchdog from another thread.
    """

    def __init__(
        self,
        gate: SafetySupervisor,
        driver: FaceDriver,
        token: str,
        full: HardwareManifest,
        *,
        generation_id: str,
        clock: Callable[[], int] = time.monotonic_ns,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if gate.state is not RunState.ARMED or gate.operator_approval is None:
            raise ValueError("face stream requires armed preflight")
        if gate.manifest.calibration_sha256 != face_manifest(full).calibration_sha256:
            raise ValueError("face stream scope mismatch")
        self.gate, self.driver, self.token, self.full = gate, driver, token, full
        self.generation_id, self.clock, self.sleep = generation_id, clock, sleeper
        self.profiles = face_profiles()
        now = clock()
        self.states = {
            n: ChannelState(gate.committed_targets[n], 0, now) for n in FACE_CHANNELS
        }
        if any(s.position != 0 for s in self.states.values()):
            raise ValueError("selected-face stream must begin at Home")
        self._started = self.last_progress_ns = now
        self._lock, self._stopped = Lock(), Event()
        self.latest_positions = {n: 0.0 for n in FACE_CHANNELS}
        self._received_ns: int | None = None
        self._sample = -1
        self._revision = 0
        self.consumed_revisions: set[int] = set()
        self.records: list[dict[str, object]] = []
        self.home_confirmed = False

    def revoke(self, detail: str) -> None:
        self._stopped.set()
        if self.gate.state is RunState.ARMED:
            self.gate.cancel_armed(detail)
        self.driver.close()

    def _check(self) -> None:
        if self._stopped.is_set() or self.gate.state is not RunState.ARMED:
            raise RuntimeError("face stream is inactive")
        now = self.clock()
        approval = self.gate.operator_approval
        if approval is None or now - approval.confirmed_monotonic_ns >= 60_000_000_000:
            raise RuntimeError("face approval expired")
        if now - self._started > 25_000_000_000:
            raise RuntimeError("face run duration exceeded")

    def offer(
        self,
        proposal: TargetUpdate,
        generation_id: str,
        audio_sample: int,
        *,
        source_monotonic_ns: int | None = None,
    ) -> None:
        try:
            self._check()
            received = (
                self.clock() if source_monotonic_ns is None else source_monotonic_ns
            )
            if not 0 <= self.clock() - received <= 250_000_000:
                raise ValueError("stale face proposal after inference")
            with self._lock:
                if generation_id != self.generation_id or audio_sample < self._sample:
                    raise ValueError("stale face generation or audio sample")
                for target in proposal.targets:
                    self.full.actuator(target.actuator_name).target_qus(
                        target.normalized_position
                    )
                for target in proposal.targets:
                    if target.actuator_name in self.latest_positions:
                        self.latest_positions[target.actuator_name] = (
                            target.normalized_position
                        )
                self._sample, self._received_ns = audio_sample, received
                self._revision += 1
        except BaseException:
            self.revoke("invalid or cancelled face source")
            raise

    def step(self, *, home: bool = False, waiting: bool = False) -> None:
        try:
            self._check()
            with self._lock:
                positions = dict(self.latest_positions)
                received, sample, revision = (
                    self._received_ns,
                    self._sample,
                    self._revision,
                )
            for name, state in self.states.items():
                self._check()
                now = self.clock()
                if not (home or waiting) and (
                    received is None or not 0 <= now - received <= 250_000_000
                ):
                    raise RuntimeError("face audio source is stale")
                dt = (now - state.sent_ns) / 1e9
                profile = self.profiles[name]
                if dt < profile.command_interval_s:
                    continue
                desired = 0.0 if home or waiting else positions[name]
                definition = self.gate.manifest.actuator(name)
                target = bounded_quantized_target(
                    state, desired, dt, profile, definition
                )
                request = PoseRequest(
                    schema_version="pose-request/v1",
                    request_id=f"face-{len(self.records)}",
                    run_id=self.gate.operator_approval.run_id,  # type: ignore[union-attr]
                    hardware_id=self.gate.manifest.hardware_id,
                    calibration_sha256=self.gate.manifest.calibration_sha256,
                    issued_monotonic_ns=now,
                    expires_monotonic_ns=now + 2_000_001,
                    targets=(
                        ActuatorTarget(actuator_name=name, normalized_position=target),
                    ),
                )
                record: dict[str, object] = {
                    "audio_sample": sample if not (home or waiting) else None,
                    "source_revision": revision,
                    "requested_position": desired,
                    "request": request.model_dump(mode="json"),
                }
                self.records.append(record)
                try:
                    receipt = self.driver.stream_face_target(self.token, request)
                except FaceTransactionError as exc:
                    record["transaction"] = exc.evidence
                    raise
                record["status"] = receipt.model_dump(mode="json")
                elapsed = (receipt.sent_monotonic_ns - state.sent_ns) / 1e9
                if not (
                    now <= receipt.sent_monotonic_ns <= now + 2_000_000
                    and receipt.sent_monotonic_ns
                    <= receipt.reported_monotonic_ns
                    <= self.clock()
                    and 0 < elapsed <= 0.25
                    and receipt.reported_monotonic_ns - self.last_progress_ns
                    <= 250_000_000
                    and receipt.target_qus == definition.target_qus(target)
                ):
                    raise RuntimeError("invalid or stalled face receipt")
                velocity = (target - state.position) / elapsed
                if (
                    abs(target - state.position) > profile.max_step + 1e-9
                    or abs(velocity) > profile.max_rate_per_s + 1e-9
                    or abs(velocity - state.velocity) / elapsed
                    > profile.max_acceleration_per_s2 + 1e-9
                ):
                    raise RuntimeError("sent face command violated independent bounds")
                state.position, state.velocity, state.sent_ns = (
                    target,
                    velocity,
                    receipt.sent_monotonic_ns,
                )
                self.last_progress_ns = receipt.reported_monotonic_ns
                self.consumed_revisions.add(revision)
        except BaseException:
            self.revoke("face stream fault; operator controls master switch")
            raise

    @property
    def at_home(self) -> bool:
        return all(
            s.position == 0 and abs(s.velocity) < 1e-9 for s in self.states.values()
        )

    def finish(self) -> None:
        try:
            self._check()
            if not self.at_home:
                raise RuntimeError("face completion requires commanded Home at rest")
            deadline = self.clock() + 2_000_000_000
            while True:
                self._check()
                snapshot = self.driver.read_only_preflight(tuple(FACE_CHANNELS))
                self.records.append(
                    {"completion_observation": snapshot.model_dump(mode="json")}
                )
                if snapshot.controller_error_register:
                    raise RuntimeError("controller error confirming face Home")
                self.last_progress_ns = snapshot.observed_monotonic_ns
                if all(
                    snapshot.positions_qus[n] == self.gate.manifest.actuator(n).home_qus
                    for n in FACE_CHANNELS
                ):
                    self._check()
                    self.driver.restore_jaw_response()
                    self.home_confirmed = True
                    self.revoke("selected face completed at controller Home")
                    return
                if self.clock() >= deadline:
                    raise RuntimeError("selected face failed to reach controller Home")
                self.sleep(0.01)
        except BaseException:
            self.revoke("face Home confirmation failed")
            raise
