"""Deterministic semantic head gestures with minimum-jerk transitions."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.affect import MonotonicNanoseconds
from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.contracts.motion import TargetUpdate, TargetUpdateHorizon
from alice.motion.controller_response import ControllerResponseConfig
from alice.motion.state import EventHistoryRecord

FinitePositiveFloat = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
NonNegativeFloat = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
SignedAmplitude = Annotated[
    float,
    Field(ge=-1.0, le=1.0, allow_inf_nan=False),
]
Asymmetry = Annotated[
    float,
    Field(ge=-0.75, le=0.75, allow_inf_nan=False),
]
_EVENT_RECORD_TYPE = "head-gesture/v1"
_NANOSECONDS_PER_SECOND = 1_000_000_000
_MINIMUM_JERK_PEAK_VELOCITY = 15.0 / 8.0
_MINIMUM_JERK_PEAK_ACCELERATION = 10.0 * math.sqrt(3.0) / 3.0


class HeadGestureKind(StrEnum):
    """Inspectable semantic head gestures."""

    NOD = "nod"
    SHAKE = "shake"
    TILT = "tilt"
    LOOK_UP = "look_up"
    LOOK_DOWN = "look_down"
    RETURN = "return"


class HeadAxisSemantics(BaseModel):
    """Manifest-derived semantic identities for the three head axes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    yaw_actuator_name: NonEmptyString
    tilt_actuator_name: NonEmptyString
    pitch_actuator_name: NonEmptyString

    @model_validator(mode="after")
    def validate_distinct_axes(self) -> HeadAxisSemantics:
        if len(set(self.actuator_names)) != 3:
            raise ValueError("semantic head axes must be distinct")
        return self

    @property
    def actuator_names(self) -> tuple[str, str, str]:
        """Return semantic axes in stable yaw, tilt, pitch order."""

        return (
            self.yaw_actuator_name,
            self.tilt_actuator_name,
            self.pitch_actuator_name,
        )

    def actuator_for(self, kind: HeadGestureKind) -> str:
        """Resolve a primitive to a semantic identity, never a channel number."""

        if kind is HeadGestureKind.SHAKE:
            return self.yaw_actuator_name
        if kind is HeadGestureKind.TILT:
            return self.tilt_actuator_name
        return self.pitch_actuator_name


class HeadGesture(BaseModel):
    """One absolute semantic gesture with bounded, replayable parameters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["head-gesture/v1"]
    gesture_id: NonEmptyString
    model_id: NonEmptyString
    model_sha256: Sha256Hex
    kind: HeadGestureKind
    starts_monotonic_ns: MonotonicNanoseconds
    actuator_name: NonEmptyString
    amplitude: SignedAmplitude
    duration_s: FinitePositiveFloat
    cycles: Annotated[int, Field(ge=1, le=4)]
    asymmetry: Asymmetry
    hold_s: NonNegativeFloat
    recovery_s: FinitePositiveFloat
    recovery_targets: tuple[ActuatorTarget, ...] = Field(min_length=1)
    initial_targets: tuple[ActuatorTarget, ...] = ()

    @model_validator(mode="after")
    def validate_parameters(self) -> HeadGesture:
        if self.amplitude == 0.0:
            raise ValueError("head gesture amplitude must be non-zero")
        if self.kind not in {HeadGestureKind.NOD, HeadGestureKind.SHAKE} and (
            self.cycles != 1 or self.asymmetry != 0.0
        ):
            raise ValueError(
                "non-oscillatory head gestures require one cycle and zero asymmetry"
            )
        names = [target.actuator_name for target in self.recovery_targets]
        if len(names) != len(set(names)):
            raise ValueError("head gesture recovery targets must be unique")
        initial_names = [target.actuator_name for target in self.initial_targets]
        if initial_names and initial_names != names:
            raise ValueError("head gesture initial targets must match recovery order")
        return self

    @property
    def ends_monotonic_ns(self) -> int:
        """Return the absolute end of the gesture recovery."""

        duration_ns = round(
            (self.duration_s + self.hold_s + self.recovery_s) * _NANOSECONDS_PER_SECOND
        )
        return self.starts_monotonic_ns + duration_ns

    def as_history_record(self) -> EventHistoryRecord:
        """Encode this gesture in the shared generic event-history envelope."""

        payload = self.model_dump(mode="json")
        return EventHistoryRecord(
            event_type=_EVENT_RECORD_TYPE,
            started_monotonic_ns=self.starts_monotonic_ns,
            ended_monotonic_ns=self.ends_monotonic_ns,
            payload={str(key): value for key, value in payload.items()},
        )

    @classmethod
    def from_history_record(cls, record: EventHistoryRecord) -> HeadGesture:
        """Decode a head record and verify its duplicated absolute interval."""

        if record.event_type != _EVENT_RECORD_TYPE:
            raise ValueError("history record is not a head gesture")
        gesture = cls.model_validate(record.payload)
        if (
            gesture.starts_monotonic_ns != record.started_monotonic_ns
            or gesture.ends_monotonic_ns != record.ended_monotonic_ns
        ):
            raise ValueError("head gesture history interval does not match payload")
        return gesture


class HeadPrimitiveGenerator:
    """Render semantic gestures as controller-feasible minimum-jerk targets."""

    def __init__(
        self,
        *,
        semantics: HeadAxisSemantics,
        controller_config: ControllerResponseConfig,
        cadence_hz: float,
    ) -> None:
        if not math.isfinite(cadence_hz) or cadence_hz <= 0.0:
            raise ValueError("head primitive cadence must be finite and positive")
        known_actuators = {
            parameters.actuator_name for parameters in controller_config.actuators
        }
        if not set(semantics.actuator_names) <= known_actuators:
            raise ValueError("head axes reference unknown semantic actuators")
        self._semantics = semantics
        self._controller_config = controller_config
        self._cadence_hz = cadence_hz

    @property
    def semantics(self) -> HeadAxisSemantics:
        return self._semantics

    def render(
        self,
        gesture: HeadGesture,
        state: TargetUpdate,
    ) -> TargetUpdateHorizon:
        """Render from the exact boundary state to exact recovery targets."""

        positions, recovery = self._validate_inputs(gesture, state)
        total_s = gesture.duration_s + gesture.hold_s + gesture.recovery_s
        segments: dict[str, tuple[tuple[float, float, float, float], ...]]
        if gesture.kind is HeadGestureKind.RETURN:
            if any(
                abs(value - recovery[actuator_name]) > abs(gesture.amplitude)
                for actuator_name, value in positions.items()
            ):
                raise ValueError("return amplitude is smaller than recovery distance")
            segments = {
                actuator_name: ((0.0, value, total_s, recovery[actuator_name]),)
                for actuator_name, value in positions.items()
            }
        else:
            actuator_name = self._semantics.actuator_for(gesture.kind)
            segments = {
                actuator_name: self._gesture_segments(
                    gesture,
                    start=positions[actuator_name],
                    recovery=recovery[actuator_name],
                )
            }
        self._validate_response_feasibility(segments)
        offsets = self._sample_offsets(total_s, segments)
        updates: list[TargetUpdate] = []
        for offset_s in offsets:
            if offset_s == total_s:
                targets = gesture.recovery_targets
            else:
                current = dict(positions)
                for actuator_name, axis_segments in segments.items():
                    current[actuator_name] = self._value_at(axis_segments, offset_s)
                targets = tuple(
                    ActuatorTarget(
                        actuator_name=target.actuator_name,
                        normalized_position=current[target.actuator_name],
                    )
                    for target in state.targets
                )
            updates.append(TargetUpdate(offset_s=offset_s, targets=targets))
        return TargetUpdateHorizon(
            schema_version="target-update-horizon/v1",
            updates=tuple(updates),
        )

    def render_window(
        self,
        gesture: HeadGesture,
        *,
        window_start_ns: int,
        horizon_s: float,
    ) -> TargetUpdateHorizon:
        """Render an absolute slice from the original persisted gesture pose."""

        if not gesture.initial_targets:
            raise ValueError("resumable head gesture is missing its initial pose")
        elapsed_s = (
            window_start_ns - gesture.starts_monotonic_ns
        ) / _NANOSECONDS_PER_SECOND
        if elapsed_s < 0.0:
            raise ValueError("head gesture window precedes gesture start")
        original = TargetUpdate(offset_s=0.0, targets=gesture.initial_targets)
        full = self.render(gesture, original)
        end_s = elapsed_s + horizon_s
        offsets = {elapsed_s, min(end_s, full.updates[-1].offset_s)}
        offsets.update(
            update.offset_s
            for update in full.updates
            if elapsed_s <= update.offset_s <= end_s
        )
        updates = tuple(
            TargetUpdate(
                offset_s=absolute_s - elapsed_s,
                targets=tuple(
                    ActuatorTarget(actuator_name=name, normalized_position=value)
                    for name, value in self._positions_at(full, absolute_s).items()
                ),
            )
            for absolute_s in sorted(offsets)
        )
        return TargetUpdateHorizon(
            schema_version="target-update-horizon/v1", updates=updates
        )

    @staticmethod
    def _positions_at(horizon: TargetUpdateHorizon, at_s: float) -> dict[str, float]:
        prior = horizon.updates[0]
        for update in horizon.updates[1:]:
            if update.offset_s >= at_s:
                span = update.offset_s - prior.offset_s
                phase = 0.0 if span == 0.0 else (at_s - prior.offset_s) / span
                right = {t.actuator_name: t.normalized_position for t in update.targets}
                return {
                    target.actuator_name: target.normalized_position
                    + (right[target.actuator_name] - target.normalized_position) * phase
                    for target in prior.targets
                }
            prior = update
        return {t.actuator_name: t.normalized_position for t in prior.targets}

    def minimum_transition_s(self, actuator_name: str, distance: float) -> float:
        """Return time needed to keep a quintic within controller derivatives."""

        if not math.isfinite(distance) or distance < 0.0:
            raise ValueError("transition distance must be finite and non-negative")
        parameters = self._controller_config.actuator(actuator_name)
        velocity_time_s = (
            _MINIMUM_JERK_PEAK_VELOCITY * distance / parameters.max_velocity_per_s
        )
        acceleration_time_s = math.sqrt(
            _MINIMUM_JERK_PEAK_ACCELERATION
            * distance
            / parameters.max_acceleration_per_s2
        )
        return max(velocity_time_s, acceleration_time_s)

    def _validate_inputs(
        self,
        gesture: HeadGesture,
        state: TargetUpdate,
    ) -> tuple[dict[str, float], dict[str, float]]:
        if state.offset_s != 0.0:
            raise ValueError("head primitive state must be at offset zero")
        names = tuple(target.actuator_name for target in state.targets)
        if set(names) != set(self._semantics.actuator_names):
            raise ValueError(
                "head primitive state must define every semantic head axis"
            )
        recovery_names = tuple(
            target.actuator_name for target in gesture.recovery_targets
        )
        if recovery_names != names:
            raise ValueError(
                "recovery targets must match ordered head state identities"
            )
        expected_actuator = self._semantics.actuator_for(gesture.kind)
        if gesture.actuator_name != expected_actuator:
            raise ValueError("gesture actuator does not match semantic kind mapping")
        positions = {
            target.actuator_name: target.normalized_position for target in state.targets
        }
        recovery = {
            target.actuator_name: target.normalized_position
            for target in gesture.recovery_targets
        }
        if gesture.kind is not HeadGestureKind.RETURN:
            changed_inactive = {
                name
                for name in names
                if name != expected_actuator and positions[name] != recovery[name]
            }
            if changed_inactive:
                raise ValueError(
                    "non-active head axes cannot change during gesture recovery"
                )
        return positions, recovery

    def _gesture_segments(
        self,
        gesture: HeadGesture,
        *,
        start: float,
        recovery: float,
    ) -> tuple[tuple[float, float, float, float], ...]:
        active_end_s = gesture.duration_s
        segments: list[tuple[float, float, float, float]] = []
        if gesture.kind in {HeadGestureKind.NOD, HeadGestureKind.SHAKE}:
            magnitude = abs(gesture.amplitude)
            direction = math.copysign(1.0, gesture.amplitude)
            denominator = 1.0 + abs(gesture.asymmetry)
            positive = direction * magnitude * (1.0 + gesture.asymmetry) / denominator
            negative = -direction * magnitude * (1.0 - gesture.asymmetry) / denominator
            lobe_s = gesture.duration_s / (2 * gesture.cycles)
            previous_t = 0.0
            previous_value = start
            for index in range(2 * gesture.cycles):
                next_t = (index + 1) * lobe_s
                next_value = start + (positive if index % 2 == 0 else negative)
                segments.append((previous_t, previous_value, next_t, next_value))
                previous_t = next_t
                previous_value = next_value
        else:
            peak = start + gesture.amplitude
            segments.append((0.0, start, active_end_s, peak))

        hold_end_s = active_end_s + gesture.hold_s
        active_value = segments[-1][3]
        if gesture.hold_s > 0.0:
            segments.append((active_end_s, active_value, hold_end_s, active_value))
        segments.append(
            (
                hold_end_s,
                active_value,
                hold_end_s + gesture.recovery_s,
                recovery,
            )
        )
        for _, left, _, right in segments:
            if not -1.0 <= left <= 1.0 or not -1.0 <= right <= 1.0:
                raise ValueError("head gesture exceeds normalized target bounds")
        return tuple(segments)

    def _validate_response_feasibility(
        self,
        by_actuator: dict[str, tuple[tuple[float, float, float, float], ...]],
    ) -> None:
        for actuator_name, segments in by_actuator.items():
            parameters = self._controller_config.actuator(actuator_name)
            for starts_s, starts_at, ends_s, ends_at in segments:
                distance = abs(ends_at - starts_at)
                if distance == 0.0:
                    continue
                available_s = ends_s - starts_s
                peak_velocity = _MINIMUM_JERK_PEAK_VELOCITY * distance / available_s
                if peak_velocity > parameters.max_velocity_per_s:
                    raise ValueError(
                        f"{actuator_name!r} transition exceeds controller response "
                        "peak velocity"
                    )
                peak_acceleration = (
                    _MINIMUM_JERK_PEAK_ACCELERATION * distance / available_s**2
                )
                if peak_acceleration > parameters.max_acceleration_per_s2:
                    raise ValueError(
                        f"{actuator_name!r} transition exceeds controller response "
                        "peak acceleration"
                    )

    def _sample_offsets(
        self,
        total_s: float,
        by_actuator: dict[str, tuple[tuple[float, float, float, float], ...]],
    ) -> tuple[float, ...]:
        period_s = 1.0 / self._cadence_hz
        sample_count = math.floor(total_s / period_s)
        offsets = {round(index * period_s, 12) for index in range(sample_count + 1)}
        offsets.add(total_s)
        for segments in by_actuator.values():
            for starts_s, _, ends_s, _ in segments:
                offsets.add(starts_s)
                offsets.add(ends_s)
        return tuple(sorted(offsets))

    @classmethod
    def _value_at(
        cls,
        segments: tuple[tuple[float, float, float, float], ...],
        offset_s: float,
    ) -> float:
        for starts_s, starts_at, ends_s, ends_at in segments:
            if offset_s <= ends_s:
                if ends_s == starts_s:
                    return ends_at
                phase = (offset_s - starts_s) / (ends_s - starts_s)
                return starts_at + (ends_at - starts_at) * cls.minimum_jerk(phase)
        return segments[-1][3]

    @staticmethod
    def minimum_jerk(phase: float) -> float:
        """Return the quintic rest-to-rest interpolation envelope."""

        clamped = max(0.0, min(1.0, phase))
        return clamped**3 * (10.0 - 15.0 * clamped + 6.0 * clamped**2)
