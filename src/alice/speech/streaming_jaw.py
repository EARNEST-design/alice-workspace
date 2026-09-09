"""Bounded jaw target streaming, with sent and observed state kept separate."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from alice.contracts.actuation import ActuatorTarget, PoseRequest
from alice.hardware.maestro_adapter import (
    MaestroPreflightSnapshot,
    MaestroStreamReceipt,
)
from alice.hardware.manifest import HardwareManifest
from alice.safety.supervisor import RunState, SafetySupervisor
from alice.speech.jaw_trial import JawTrialConfig, next_jaw_target


class JawStreamingDriver(Protocol):
    def stream_jaw_target(
        self, explicit_enable_token: str, request: PoseRequest
    ) -> MaestroStreamReceipt: ...
    def read_only_preflight(
        self, actuator_names: tuple[str, ...]
    ) -> MaestroPreflightSnapshot: ...
    def restore_jaw_response(self) -> None: ...
    def close(self) -> None: ...


class StreamingJawCommandStream:
    """Use the supervisor's preflight/arming gate, then bound sent targets.

    This mode never issues normal APPLIED permits or fabricates settled output.
    The deterministic trajectory guard validates each sent target; actual PWM is
    observed separately. Normal completion independently polls exact Home.
    """

    def __init__(
        self,
        gate: SafetySupervisor,
        driver: JawStreamingDriver,
        token: str,
        config: JawTrialConfig,
        *,
        clock: Callable[[], int],
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if gate.state is not RunState.ARMED or gate.operator_approval is None:
            raise ValueError("stream requires the armed operator/preflight gate")
        if tuple(a.name for a in gate.manifest.actuators) != ("mouth_open",):
            raise ValueError("stream requires the single jaw manifest")
        self._gate, self._driver, self._token = gate, driver, token
        self.config, self.clock, self._sleep = config, clock, sleeper
        self.position = gate.committed_targets["mouth_open"]
        if not config.closed_position <= self.position <= config.open_position:
            raise ValueError("measured start is outside the streaming jaw range")
        self.velocity = 0.0
        self.last_progress_ns = clock()
        self.last_sent_ns = self.last_progress_ns
        self._started = self.last_progress_ns
        self._active = True
        self.records: list[dict[str, object]] = []

    @property
    def manifest(self) -> HardwareManifest:
        return self._gate.manifest

    def revoke(self, detail: str) -> None:
        self._active = False
        if self._gate.state is RunState.ARMED:
            self._gate.cancel_armed(detail)

    def close(self) -> None:
        self._driver.close()

    def step(
        self, desired: float, *, audio_sample: int | None
    ) -> MaestroStreamReceipt | None:
        if not self._active or self._gate.state is not RunState.ARMED:
            raise RuntimeError("jaw stream is inactive")
        now = self.clock()
        elapsed = (now - self.last_sent_ns) / 1e9
        if elapsed < self.config.command_interval_s:
            return None
        try:
            approval = self._gate.operator_approval
            if (
                approval is None
                or now - approval.confirmed_monotonic_ns >= 60_000_000_000
            ):
                raise RuntimeError("stream approval expired")
            if (now - self._started) / 1e9 > self.config.max_run_s:
                raise RuntimeError("jaw stream duration exceeded")
            jaw = self.manifest.actuator("mouth_open")
            epsilon = 0.5 / max(
                jaw.home_qus - jaw.software_min_qus, jaw.software_max_qus - jaw.home_qus
            )
            target = next_jaw_target(
                self.position,
                self.velocity,
                desired,
                elapsed,
                self.config,
                endpoint_tolerance=epsilon,
            )
            velocity = (target - self.position) / elapsed
            if (
                abs(target - self.position) > self.config.max_step + 1e-9
                or abs(velocity) > self.config.max_rate_per_s + 1e-9
                or abs(velocity - self.velocity) / elapsed
                > self.config.max_acceleration_per_s2 + 1e-9
            ):
                raise RuntimeError("stream target failed independent motion bounds")
            request = PoseRequest(
                schema_version="pose-request/v1",
                request_id=f"jaw-stream-{len(self.records)}",
                run_id=approval.run_id,
                hardware_id=self.manifest.hardware_id,
                calibration_sha256=self.manifest.calibration_sha256,
                issued_monotonic_ns=now,
                expires_monotonic_ns=now + 500_000_000,
                targets=(
                    ActuatorTarget(
                        actuator_name="mouth_open", normalized_position=target
                    ),
                ),
            )
            record: dict[str, object] = {
                "audio_sample": audio_sample,
                "requested_position": desired,
                "request": request.model_dump(mode="json"),
            }
            self.records.append(record)
            receipt = self._driver.stream_jaw_target(self._token, request)
            record["status"] = receipt.model_dump(mode="json")
            actual_elapsed = (receipt.sent_monotonic_ns - self.last_sent_ns) / 1e9
            if (
                actual_elapsed <= 0
                or actual_elapsed > self.config.max_gap_s
                or not now <= receipt.sent_monotonic_ns <= now + 2_000_000
                or not receipt.sent_monotonic_ns <= receipt.reported_monotonic_ns
                or (receipt.reported_monotonic_ns - self.last_progress_ns) / 1e9
                > self.config.max_gap_s
            ):
                raise RuntimeError("invalid or stalled streaming receipt clock")
            self.velocity = (target - self.position) / actual_elapsed
            self.position = target
            self.last_sent_ns = receipt.sent_monotonic_ns
            self.last_progress_ns = receipt.reported_monotonic_ns
            return receipt
        except BaseException as exc:
            self.revoke("streaming target fault; operator controls master switch")
            self._close_after_fault(exc)
            raise

    def finish(self) -> None:
        try:
            if not self._active or self.position != 0 or abs(self.velocity) > 1e-6:
                raise RuntimeError(
                    "stream cannot finish before commanding Home at rest"
                )
            deadline = self.clock() + 500_000_000
            while True:
                snapshot = self._driver.read_only_preflight(("mouth_open",))
                self.records.append(
                    {"completion_observation": snapshot.model_dump(mode="json")}
                )
                if snapshot.controller_error_register:
                    raise RuntimeError("controller error while confirming Home")
                if (
                    snapshot.positions_qus["mouth_open"]
                    == self.manifest.actuator("mouth_open").home_qus
                ):
                    if not self._active:
                        raise RuntimeError("stream cancelled during Home confirmation")
                    self._driver.restore_jaw_response()
                    self.revoke("stream completed at observed controller Home")
                    return
                if self.clock() >= deadline:
                    raise RuntimeError("stream did not settle at controller Home")
                self._sleep(0.01)
        except BaseException as exc:
            self.revoke("stream completion failed")
            self._close_after_fault(exc)
            raise

    def _close_after_fault(self, primary: BaseException) -> None:
        try:
            self.close()
        except BaseException as cleanup:
            detail = f"stream close: {type(cleanup).__name__}: {cleanup}"
            primary.add_note(detail)
            self.records.append({"cleanup_error": detail})
