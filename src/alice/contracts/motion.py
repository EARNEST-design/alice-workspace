"""Hardware-independent contracts for sparse motion proposals."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.blendshapes import NonEmptyString, Sha256Hex

MonotonicNanoseconds = Annotated[int, Field(ge=0)]
OffsetSeconds = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
ReplaySeed = Annotated[int, Field(ge=0)]
MotionSupportStatus = Literal[
    "supported",
    "interpolated",
    "fallback",
    "stale",
]


class TargetUpdate(BaseModel):
    """A sparse set of semantic actuator targets at a horizon-relative time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    offset_s: OffsetSeconds
    targets: tuple[ActuatorTarget, ...]

    @model_validator(mode="after")
    def validate_targets(self) -> TargetUpdate:
        if not self.targets:
            raise ValueError("target update must contain at least one target")
        names = [target.actuator_name for target in self.targets]
        if len(names) != len(set(names)):
            raise ValueError("targets must have a unique actuator_name")
        return self


class TargetUpdateHorizon(BaseModel):
    """An ordered finite sequence of sparse target updates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["target-update-horizon/v1"]
    updates: tuple[TargetUpdate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_offsets(self) -> TargetUpdateHorizon:
        offsets = [update.offset_s for update in self.updates]
        if any(current <= previous for previous, current in zip(offsets, offsets[1:])):
            raise ValueError("target update offsets must be strictly increasing")
        return self


class MotionProposal(BaseModel):
    """A replayable, identity-bound proposal with no actuation authority.

    ``expires_monotonic_ns`` is an exclusive deadline: every horizon update is
    scheduled strictly before it, and the proposal is unusable at the deadline.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["motion-proposal/v1"]
    proposal_id: NonEmptyString
    run_id: NonEmptyString
    generated_monotonic_ns: MonotonicNanoseconds
    expires_monotonic_ns: MonotonicNanoseconds
    seed: ReplaySeed
    model_id: NonEmptyString
    model_sha256: Sha256Hex
    calibration_sha256: Sha256Hex
    controller_settings_sha256: Sha256Hex
    support_status: MotionSupportStatus
    horizon: TargetUpdateHorizon

    @model_validator(mode="after")
    def validate_validity_window(self) -> MotionProposal:
        if self.expires_monotonic_ns <= self.generated_monotonic_ns:
            raise ValueError("motion proposal must expire after generation")
        validity_s = (
            self.expires_monotonic_ns - self.generated_monotonic_ns
        ) / 1_000_000_000
        if any(update.offset_s >= validity_s for update in self.horizon.updates):
            raise ValueError(
                "motion proposal updates must be strictly before proposal expiry"
            )
        return self

    def is_expired(self, *, now_monotonic_ns: int) -> bool:
        """Return true at or after the exclusive proposal deadline."""

        return now_monotonic_ns >= self.expires_monotonic_ns
