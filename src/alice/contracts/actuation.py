"""Versioned semantic contracts at the safety-supervisor boundary."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.blendshapes import NonEmptyString, Sha256Hex

NormalizedPosition = Annotated[
    float,
    Field(ge=-1.0, le=1.0, allow_inf_nan=False),
]
MonotonicNanoseconds = Annotated[int, Field(ge=0)]


class ActuatorTarget(BaseModel):
    """One semantic actuator target in its reviewed normalized range."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actuator_name: NonEmptyString
    normalized_position: NormalizedPosition


class PoseRequest(BaseModel):
    """A bounded-lifetime pose request submitted to the safety supervisor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["pose-request/v1"]
    request_id: NonEmptyString
    run_id: NonEmptyString
    hardware_id: NonEmptyString
    calibration_sha256: Sha256Hex
    issued_monotonic_ns: MonotonicNanoseconds
    expires_monotonic_ns: MonotonicNanoseconds
    targets: tuple[ActuatorTarget, ...]

    @model_validator(mode="after")
    def validate_request(self) -> PoseRequest:
        if self.expires_monotonic_ns <= self.issued_monotonic_ns:
            raise ValueError("pose request must expire after issuance")
        names = [target.actuator_name for target in self.targets]
        if len(names) != len(set(names)):
            raise ValueError("targets must have a unique actuator_name")
        if not self.targets:
            raise ValueError("pose request must contain at least one target")
        return self

    def is_expired(self, *, now_monotonic_ns: int) -> bool:
        """Return true at or after the exclusive command deadline."""

        return now_monotonic_ns >= self.expires_monotonic_ns


class ActuatorStatusState(StrEnum):
    APPLIED = "applied"
    REJECTED = "rejected"
    FAULT = "fault"


class ControllerOutputSample(BaseModel):
    """One controller-reported output sample; never mechanical-position proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actuator_name: NonEmptyString
    # Zero is a real Maestro observation: PWM is disabled. It cannot match a
    # positive target, but must be retainable while waiting for initial enable.
    observed_qus: Annotated[int, Field(ge=0)]
    target_qus: Annotated[int, Field(gt=0)]
    observed_monotonic_ns: MonotonicNanoseconds


class ActuatorStatus(BaseModel):
    """Status returned by an actuator adapter for one pose request.

    ``APPLIED`` reports a completed application, ``REJECTED`` means nothing was
    forwarded, and ``FAULT`` may preserve the subset known to have been applied
    before an error. ``applied_targets`` means the controller reported the
    commanded pulse/output target. It does not establish mechanical linkage,
    face, or visually settled position; those require independent verification.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["actuator-status/v1"]
    request_id: NonEmptyString
    run_id: NonEmptyString
    hardware_id: NonEmptyString
    calibration_sha256: Sha256Hex
    reported_monotonic_ns: MonotonicNanoseconds
    state: ActuatorStatusState
    applied_targets: tuple[ActuatorTarget, ...] = Field(
        default=(),
        description=(
            "Targets whose commanded pulse/output was confirmed by the controller; "
            "not proof of mechanical or visual position."
        ),
    )
    fault_code: NonEmptyString | None = None
    detail: NonEmptyString | None = None
    controller_output_samples: tuple[ControllerOutputSample, ...] = ()
    targets_reached: bool | None = None

    @model_validator(mode="after")
    def validate_status(self) -> ActuatorStatus:
        names = [target.actuator_name for target in self.applied_targets]
        if len(names) != len(set(names)):
            raise ValueError("applied targets must have a unique actuator_name")
        if self.targets_reached is True and not self.controller_output_samples:
            raise ValueError("targets_reached requires controller output samples")
        if any(
            sample.observed_monotonic_ns > self.reported_monotonic_ns
            for sample in self.controller_output_samples
        ):
            raise ValueError("controller output sample cannot postdate status")
        if self.state is ActuatorStatusState.APPLIED:
            if not self.applied_targets:
                raise ValueError("an applied status requires applied_targets")
            if self.fault_code is not None:
                raise ValueError("an applied status cannot carry a fault_code")
            if self.targets_reached is False:
                raise ValueError("applied status cannot report targets_reached false")
        elif self.state is ActuatorStatusState.REJECTED:
            if self.applied_targets:
                raise ValueError("a rejected status cannot carry applied_targets")
            if self.fault_code is None:
                raise ValueError("a rejected or fault status requires a fault_code")
        elif self.fault_code is None:
            # FAULT deliberately permits partial targets to preserve physical-state
            # evidence after a short write or controller failure.
            raise ValueError("a rejected or fault status requires a fault_code")
        return self
