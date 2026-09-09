"""Bounded mouth-only command planning; imports never access hardware."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.actuation import ActuatorStatus, ActuatorTarget, PoseRequest
from alice.hardware.adapter import (
    ActuatorAdapter,
    ActuatorAuthorization,
    AdapterIdentity,
)
from alice.hardware.manifest import HardwareManifest
from alice.safety.supervisor import RunState, SafetyLimits, SafetySupervisor


class JawTrialConfig(BaseModel):
    """Trial command caps, not measured mechanical response guarantees."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["jaw-speech-trial/v1"] = "jaw-speech-trial/v1"
    closed_position: float = Field(default=-1, ge=-1, le=0, allow_inf_nan=False)
    open_position: float = Field(default=0.6, gt=0, le=1.0, allow_inf_nan=False)
    max_step: float = Field(default=0.1, gt=0, le=0.4, allow_inf_nan=False)
    max_rate_per_s: float = Field(default=2, gt=0, le=10, allow_inf_nan=False)
    max_acceleration_per_s2: float = Field(default=4, gt=0, le=200, allow_inf_nan=False)
    response_time_s: float = Field(default=0.18, ge=0.02, le=0.3, allow_inf_nan=False)
    mouth_lead_s: float = Field(default=0, ge=0, le=0.2, allow_inf_nan=False)
    command_interval_s: float = Field(default=0.04, ge=0.04, le=0.1)
    max_gap_s: float = Field(default=0.25, ge=0.1, le=0.25)
    source_timeout_s: float = Field(default=0.25, ge=0.1, le=0.25)
    phase_timeout_s: float = Field(default=6, gt=0, le=8)
    max_audio_s: float = Field(default=10, gt=0, le=10)
    max_run_s: float = Field(default=25, gt=0, le=30)

    @model_validator(mode="after")
    def ordered(self) -> JawTrialConfig:
        if self.closed_position >= self.open_position:
            raise ValueError("jaw opening must exceed closure")
        return self


def next_jaw_target(
    position: float,
    velocity: float,
    desired: float,
    elapsed_s: float,
    config: JawTrialConfig,
    *,
    endpoint_tolerance: float = 1e-6,
) -> float:
    """Choose a bounded target from the last acknowledged command state.

    Reserve stopping distance to both absolute endpoints. Position/velocity are
    command differences, not measured jaw pose. Delays must not create a burst
    of overdue targets; a stalled scheduling clock aborts instead.
    """
    if not all(math.isfinite(v) for v in (position, velocity, desired, elapsed_s)):
        raise ValueError("nonfinite jaw state or clock")
    if not config.command_interval_s <= elapsed_s <= config.max_gap_s:
        raise ValueError("jaw command clock is invalid or stalled")
    if not config.closed_position <= position <= config.open_position:
        raise ValueError("acknowledged jaw target is outside trial bounds")
    if abs(velocity) > config.max_rate_per_s:
        raise ValueError("acknowledged jaw velocity is outside trial caps")
    desired = max(config.closed_position, min(config.open_position, desired))
    # Leave numerical margin for the independent supervisor's strict checks.
    acceleration = config.max_acceleration_per_s2 * 0.98
    rate = config.max_rate_per_s * 0.98
    dt = elapsed_s
    slack = 0.002
    minimum_dt = config.command_interval_s
    # Worst-case guaranteed deceleration when authorization may occur later
    # than planning, but the preceding command was acknowledged immediately.
    guaranteed_braking = (
        acceleration * (1 + slack / minimum_dt) ** 2 - rate * slack / minimum_dt**2
    )
    if guaranteed_braking <= 0:
        raise ValueError(
            "trial caps cannot accommodate authorization clock uncertainty"
        )
    braking = min(acceleration, guaranteed_braking) * 0.8

    def safe_speed(distance: float) -> float:
        return math.sqrt((braking * dt) ** 2 + 2 * braking * distance) - braking * dt

    low = max(
        -rate,
        -config.max_step / dt,
        velocity - acceleration * dt,
        -safe_speed(position - config.closed_position),
    )
    high = min(
        rate,
        config.max_step / dt,
        velocity + acceleration * dt,
        safe_speed(config.open_position - position),
    )
    # The supervisor samples the clock after request construction. Intersect
    # acceleration bounds for every possible authorization time in the next
    # 2 ms, including extrema of v*t +/- a*t². Do not weaken its limits.
    times = [dt, dt + slack]
    for extremum in (velocity / (2 * acceleration), -velocity / (2 * acceleration)):
        if dt < extremum < dt + slack:
            times.append(extremum)
    low = max(low, max(velocity * t - acceleration * t * t for t in times) / dt)
    high = min(high, min(velocity * t + acceleration * t * t for t in times) / dt)
    if low > high + 1e-12:
        raise ValueError("no feasible jaw command within the trial caps")
    error = desired - position
    if not math.isfinite(endpoint_tolerance) or endpoint_tolerance < 0:
        raise ValueError("invalid endpoint tolerance")
    goal_velocity = error / (
        dt if abs(error) <= endpoint_tolerance else max(dt, config.response_time_s)
    )
    chosen = max(low, min(high, goal_velocity))
    target = position + chosen * dt
    return max(config.closed_position, min(config.open_position, target))


def trial_limits(config: JawTrialConfig | None = None) -> SafetyLimits:
    config = config or JawTrialConfig()
    return SafetyLimits(
        max_step=config.max_step,
        max_rate_per_second=config.max_rate_per_s,
        max_acceleration_per_second_squared=config.max_acceleration_per_s2,
        watchdog_timeout_ns=1_000_000_000,
        approval_max_age_ns=60_000_000_000,
        preflight_max_age_ns=60_000_000_000,
        command_max_age_ns=500_000_000,
        recovery_command_ttl_ns=500_000_000,
    )


class JawPlaybackStream(Protocol):
    config: JawTrialConfig
    clock: Callable[[], int]
    position: float
    velocity: float
    records: list[dict[str, object]]

    @property
    def manifest(self) -> HardwareManifest: ...
    @property
    def last_progress_ns(self) -> int: ...
    def step(self, desired: float, *, audio_sample: int | None) -> object | None: ...
    def finish(self) -> None: ...
    def revoke(self, detail: str) -> None: ...
    def close(self) -> None: ...


class MouthOnlyAdapter:
    """Final channel and absolute-range boundary around a permit-aware adapter."""

    def __init__(self, adapter: ActuatorAdapter, config: JawTrialConfig) -> None:
        self._adapter = adapter
        self._config = config

    @property
    def identity(self) -> AdapterIdentity:
        return self._adapter.identity

    def apply(self, authorization: ActuatorAuthorization) -> ActuatorStatus:
        request = authorization.request
        if (
            request is None
            or len(request.targets) != 1
            or request.targets[0].actuator_name != "mouth_open"
        ):
            raise ValueError("trial permits mouth_open only")
        position = request.targets[0].normalized_position
        if not self._config.closed_position <= position <= self._config.open_position:
            raise ValueError("jaw target exceeds the approved absolute trial range")
        return self._adapter.apply(authorization)

    def close(self) -> None:
        self._adapter.close()


class JawCommandStream:
    """One in-flight request, always reconciled to the actual APPLIED time."""

    def __init__(
        self,
        supervisor: SafetySupervisor,
        adapter: MouthOnlyAdapter,
        config: JawTrialConfig,
        *,
        clock: Callable[[], int],
    ) -> None:
        if supervisor.state is not RunState.RUNNING:
            raise ValueError("jaw stream requires a started supervisor")
        initial = supervisor.committed_targets["mouth_open"]
        if not config.closed_position <= initial <= config.open_position:
            raise ValueError("measured jaw start is outside trial bounds")
        self.supervisor = supervisor
        self.adapter = adapter
        self.config = config
        self.clock = clock
        self.position = initial
        self.velocity = 0.0
        self.last_ack_ns = supervisor.last_applied_by_actuator["mouth_open"]
        self.records: list[dict[str, object]] = []

    @property
    def manifest(self) -> HardwareManifest:
        return self.supervisor.manifest

    @property
    def last_progress_ns(self) -> int:
        return self.last_ack_ns

    def revoke(self, detail: str) -> None:
        self.supervisor.revoke_external_authority(detail)

    def close(self) -> None:
        self.adapter.close()

    def finish(self) -> None:
        if not self.supervisor.complete_run().accepted:
            raise RuntimeError("jaw completion did not confirm controller Home")

    def step(
        self, desired: float, *, audio_sample: int | None
    ) -> ActuatorStatus | None:
        now = self.clock()
        elapsed = (now - self.last_ack_ns) / 1e9
        if elapsed < self.config.command_interval_s:
            return None
        try:
            jaw = self.supervisor.manifest.actuator("mouth_open")
            # Stop asymptotic approach within half a calibrated pulse unit,
            # then request the exact endpoint through the same dynamic limits.
            endpoint_tolerance = 0.5 / max(
                jaw.home_qus - jaw.software_min_qus,
                jaw.software_max_qus - jaw.home_qus,
            )
            target = next_jaw_target(
                self.position,
                self.velocity,
                desired,
                elapsed,
                self.config,
                endpoint_tolerance=endpoint_tolerance,
            )
            manifest = self.supervisor.manifest
            approval = self.supervisor.operator_approval
            if approval is None:
                raise RuntimeError("jaw trial has no approval")
            request = PoseRequest(
                schema_version="pose-request/v1",
                request_id=f"jaw-{len(self.records)}",
                run_id=approval.run_id,
                hardware_id=manifest.hardware_id,
                calibration_sha256=manifest.calibration_sha256,
                issued_monotonic_ns=now,
                expires_monotonic_ns=now + 500_000_000,
                targets=(
                    ActuatorTarget(
                        actuator_name="mouth_open", normalized_position=target
                    ),
                ),
            )
            if self.clock() - now > 2_000_000:
                raise RuntimeError(
                    "jaw planning exceeded its authorization time budget"
                )
            decision = self.supervisor.authorize(request)
            if not decision.authorized:
                raise RuntimeError(f"jaw supervisor rejected target: {decision.fault}")
            record: dict[str, object] = {
                "audio_sample": audio_sample,
                "requested_position": desired,
                "request": request.model_dump(mode="json"),
            }
            self.records.append(record)
            status = self.adapter.apply(decision)
            record["status"] = status.model_dump(mode="json")
            result = self.supervisor.record_status(status)
            if not result.accepted or not status.targets_reached:
                raise RuntimeError(
                    f"jaw controller failed: {status.fault_code or result.fault}"
                )
            actual_elapsed = (status.reported_monotonic_ns - self.last_ack_ns) / 1e9
            if actual_elapsed <= 0:
                raise RuntimeError("invalid jaw acknowledgement clock")
            self.velocity = (target - self.position) / actual_elapsed
            self.position = target
            self.last_ack_ns = status.reported_monotonic_ns
            return status
        except BaseException:
            self.supervisor.revoke_external_authority(
                "jaw stream stopped; physical power removal required"
            )
            raise
