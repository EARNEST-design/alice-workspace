"""Hardware-independent contracts for sparse motion proposals."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.blendshapes import NonEmptyString, Sha256Hex

MonotonicNanoseconds = Annotated[int, Field(ge=0)]
OffsetSeconds = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
ReplaySeed = Annotated[int, Field(ge=0)]


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
    """A replayable, identity-bound proposal with no actuation authority."""

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
    horizon: TargetUpdateHorizon

    @model_validator(mode="after")
    def validate_validity_window(self) -> MotionProposal:
        if self.expires_monotonic_ns <= self.generated_monotonic_ns:
            raise ValueError("motion proposal must expire after generation")
        return self

    def is_expired(self, *, now_monotonic_ns: int) -> bool:
        """Return true at or after the exclusive proposal deadline."""

        return now_monotonic_ns >= self.expires_monotonic_ns
