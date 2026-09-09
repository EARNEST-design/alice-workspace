"""Serializable continuation state for hardware-independent motion generation."""

from __future__ import annotations

from typing import Annotated, Literal

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from alice.contracts.affect import MonotonicNanoseconds
from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.contracts.motion import TargetUpdate
from alice.motion.intent_filter import FilteredIntent

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
TorchRngByte = Annotated[int, Field(ge=0, le=255)]


class ActuatorVelocity(BaseModel):
    """Estimated normalized velocity for one semantic actuator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actuator_name: NonEmptyString
    velocity_per_s: FiniteFloat


class EventHistoryRecord(BaseModel):
    """Typed, JSON-safe event record shared by concrete event schemas."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: NonEmptyString
    started_monotonic_ns: MonotonicNanoseconds
    ended_monotonic_ns: MonotonicNanoseconds
    payload: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_interval(self) -> EventHistoryRecord:
        if self.ended_monotonic_ns < self.started_monotonic_ns:
            raise ValueError("event end must not precede event start")
        return self


class ProceduralEvent(BaseModel):
    """One active or next procedural event on the absolute monotonic clock."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    starts_monotonic_ns: MonotonicNanoseconds
    transition_in_s: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    hold_s: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    transition_out_s: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    amplitude: Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]

    @property
    def ends_monotonic_ns(self) -> int:
        return self.starts_monotonic_ns + round(
            (self.transition_in_s + self.hold_s + self.transition_out_s) * 1e9
        )


class ProceduralContinuation(BaseModel):
    """Accepted procedural phase and scheduler state, excluding future lookahead."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seed: Annotated[int, Field(ge=0)]
    epoch_monotonic_ns: MonotonicNanoseconds
    monotonic_ns: MonotonicNanoseconds
    drift: dict[
        str,
        tuple[
            tuple[
                Annotated[float, Field(gt=0, allow_inf_nan=False)],
                FiniteFloat,
            ],
            ...,
        ],
    ]
    blink: ProceduralEvent
    gaze: ProceduralEvent
    numpy_rng_state: dict[str, JsonValue]
    applied_variation: dict[str, FiniteFloat]

    @model_validator(mode="after")
    def validate_schedule(self) -> ProceduralContinuation:
        if self.epoch_monotonic_ns > self.monotonic_ns:
            raise ValueError("procedural epoch cannot postdate its boundary")
        for event in (self.blink, self.gaze):
            if event.starts_monotonic_ns < self.epoch_monotonic_ns:
                raise ValueError("procedural event cannot predate its epoch")
            if event.ends_monotonic_ns <= self.monotonic_ns:
                raise ValueError("procedural pending event already ended")
        if any(not components for components in self.drift.values()):
            raise ValueError("procedural drift requires frequency/phase components")
        try:
            rng = np.random.default_rng()
            rng.bit_generator.state = self.numpy_rng_state
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError("invalid procedural RNG state") from error
        return self


class GeneratorState(BaseModel):
    """Complete replay state at the end of one accepted motion prefix."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["generator-state/v1"]
    last_accepted_target: TargetUpdate
    last_reported_pose: TargetUpdate
    estimated_velocity: tuple[ActuatorVelocity, ...]
    filtered_intent: FilteredIntent
    latent_vector: tuple[FiniteFloat, ...]
    numpy_rng_state: dict[str, JsonValue]
    torch_rng_state: tuple[TorchRngByte, ...]
    event_history: tuple[EventHistoryRecord, ...]
    procedural_continuation: ProceduralContinuation | None = None
    model_id: NonEmptyString
    model_sha256: Sha256Hex
    calibration_sha256: Sha256Hex
    controller_settings_sha256: Sha256Hex
    monotonic_ns: MonotonicNanoseconds

    @field_validator("numpy_rng_state", mode="before")
    @classmethod
    def normalize_numpy_rng_state(cls, value: object) -> object:
        """Convert NumPy-owned arrays and integers to portable JSON values."""

        return _json_safe_numpy_value(value)

    @model_validator(mode="after")
    def validate_continuation_boundary(self) -> GeneratorState:
        if self.last_accepted_target.offset_s != 0.0:
            raise ValueError("last accepted target must be at the state boundary")
        if self.last_reported_pose.offset_s != 0.0:
            raise ValueError("last reported pose must be at the state boundary")

        accepted_names = {
            target.actuator_name for target in self.last_accepted_target.targets
        }
        reported_names = {
            target.actuator_name for target in self.last_reported_pose.targets
        }
        velocity_names = [item.actuator_name for item in self.estimated_velocity]
        if len(velocity_names) != len(set(velocity_names)):
            raise ValueError("estimated velocities must have unique actuator names")
        if reported_names != accepted_names or set(velocity_names) != accepted_names:
            raise ValueError(
                "accepted target, reported pose, and velocity identities must match"
            )
        if self.filtered_intent.accepted_monotonic_ns > self.monotonic_ns:
            raise ValueError("filtered intent cannot postdate generator state")
        if self.procedural_continuation is not None:
            continuation = self.procedural_continuation
            if continuation.monotonic_ns != self.monotonic_ns:
                raise ValueError("procedural continuation must match state boundary")
            if set(continuation.applied_variation) != accepted_names:
                raise ValueError("procedural variation must cover accepted actuators")
            if not set(continuation.drift) <= accepted_names:
                raise ValueError("procedural drift contains unknown actuators")
        if not self.numpy_rng_state:
            raise ValueError("NumPy RNG state must not be empty")
        if not self.torch_rng_state:
            raise ValueError("Torch RNG state must not be empty")
        return self


def dump_state(state: GeneratorState) -> str:
    """Encode generator state as validated, portable JSON."""

    return state.model_dump_json()


def load_state(serialized: str | bytes) -> GeneratorState:
    """Restore generator state from its strict JSON envelope."""

    return GeneratorState.model_validate_json(serialized)


def _json_safe_numpy_value(value: object) -> object:
    if isinstance(value, np.ndarray):
        return _json_safe_numpy_value(value.tolist())
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, dict):
        return {str(key): _json_safe_numpy_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_numpy_value(item) for item in value]
    return value
