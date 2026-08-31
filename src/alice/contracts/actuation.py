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


class ActuatorStatus(BaseModel):
    """Status returned by an actuator adapter for one pose request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["actuator-status/v1"]
    request_id: NonEmptyString
    run_id: NonEmptyString
    hardware_id: NonEmptyString
    calibration_sha256: Sha256Hex
    reported_monotonic_ns: MonotonicNanoseconds
    state: ActuatorStatusState
    applied_targets: tuple[ActuatorTarget, ...] = ()
    fault_code: NonEmptyString | None = None
    detail: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_status(self) -> ActuatorStatus:
        names = [target.actuator_name for target in self.applied_targets]
        if len(names) != len(set(names)):
            raise ValueError("applied targets must have a unique actuator_name")
        if self.state is ActuatorStatusState.APPLIED:
            if not self.applied_targets:
                raise ValueError("an applied status requires applied_targets")
            if self.fault_code is not None:
                raise ValueError("an applied status cannot carry a fault_code")
        else:
            if self.applied_targets:
                raise ValueError(
                    "a rejected or fault status cannot carry applied_targets"
                )
            if self.fault_code is None:
                raise ValueError("a rejected or fault status requires a fault_code")
        return self
