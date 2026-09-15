"""Seeded, affect-conditioned configured scheduling for semantic head gestures."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.contracts.motion import TargetUpdate
from alice.motion.controller_response import ControllerResponseConfig
from alice.motion.head_primitives import (
    HeadAxisSemantics,
    HeadGesture,
    HeadGestureKind,
    HeadPrimitiveGenerator,
)
from alice.motion.intent_filter import FilteredIntent, SupportStatus
from alice.motion.state import EventHistoryRecord

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
FinitePositiveFloat = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
_NANOSECONDS_PER_SECOND = 1_000_000_000
_DECISION_RECORD_TYPE = "head-decision-state/v1"


class FloatRange(BaseModel):
    """Closed finite sampling interval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum: FiniteFloat
    maximum: FiniteFloat

    @model_validator(mode="after")
    def validate_order(self) -> FloatRange:
        if self.minimum > self.maximum:
            raise ValueError("range minimum must not exceed maximum")
        return self


class IntegerRange(BaseModel):
    """Closed integer sampling interval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum: Annotated[int, Field(ge=1, le=4)]
    maximum: Annotated[int, Field(ge=1, le=4)]

    @model_validator(mode="after")
    def validate_order(self) -> IntegerRange:
        if self.minimum > self.maximum:
            raise ValueError("range minimum must not exceed maximum")
        return self


class HeadGesturePolicy(BaseModel):
    """Configured hazard and bounded primitive parameters for one gesture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: HeadGestureKind
    actuator_name: NonEmptyString
    base_hazard_hz: FinitePositiveFloat
    min_hazard_hz: FinitePositiveFloat
    max_hazard_hz: FinitePositiveFloat
    affect_weights: tuple[FiniteFloat, ...] = Field(min_length=1)
    intensity_weight: FiniteFloat
    refractory_s: FinitePositiveFloat
    recovery_time_constant_s: FinitePositiveFloat
    amplitude: FloatRange
    duration_s: FloatRange
    cycles: IntegerRange
    asymmetry: FloatRange
    hold_s: FloatRange
    recovery_s: FloatRange
    polarity: Literal["positive", "negative", "bidirectional"]
    provenance: NonEmptyString

    @model_validator(mode="after")
    def validate_policy(self) -> HeadGesturePolicy:
        if self.kind is HeadGestureKind.RETURN:
            raise ValueError("return-to-attention is a primitive, not a hazard event")
        if not self.min_hazard_hz <= self.base_hazard_hz <= self.max_hazard_hz:
            raise ValueError("base hazard must lie within configured bounds")
        if self.amplitude.minimum <= 0.0 or self.amplitude.maximum > 1.0:
            raise ValueError("amplitude range must lie in (0, 1]")
        if self.duration_s.minimum <= 0.0:
            raise ValueError("duration range must be positive")
        if self.hold_s.minimum < 0.0:
            raise ValueError("hold range must be non-negative")
        if self.recovery_s.minimum <= 0.0:
            raise ValueError("recovery range must be positive")
        if not -0.75 <= self.asymmetry.minimum <= self.asymmetry.maximum <= 0.75:
            raise ValueError("asymmetry range must lie in [-0.75, 0.75]")
        if self.kind not in {HeadGestureKind.NOD, HeadGestureKind.SHAKE} and (
            self.cycles.minimum != 1 or self.cycles.maximum != 1
        ):
            raise ValueError("non-oscillatory gestures require exactly one cycle")
        if self.kind not in {HeadGestureKind.NOD, HeadGestureKind.SHAKE} and (
            self.asymmetry.minimum != 0.0 or self.asymmetry.maximum != 0.0
        ):
            raise ValueError("non-oscillatory gestures require zero asymmetry")
        expected_polarity = {
            HeadGestureKind.LOOK_UP: "positive",
            HeadGestureKind.LOOK_DOWN: "negative",
        }.get(self.kind)
        if expected_polarity is not None and self.polarity != expected_polarity:
            raise ValueError(
                f"{self.kind.value} gestures require {expected_polarity!r} polarity"
            )
        return self


class HeadGestureConfig(BaseModel):
    """Versioned configured-prior scheduler and semantic primitive policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["head-gesture-config/v1"]
    model_id: NonEmptyString
    scheduler_kind: Literal["configured-prior"]
    labeled_gesture_episodes: Literal[0]
    affect_schema_id: NonEmptyString
    affect_dimensions: tuple[NonEmptyString, ...] = Field(min_length=1)
    controller_response_model_id: NonEmptyString
    calibration_sha256: Sha256Hex
    controller_settings_sha256: Sha256Hex
    cadence_hz: FinitePositiveFloat
    decision_interval_s: FinitePositiveFloat
    global_refractory_s: FinitePositiveFloat
    semantics: HeadAxisSemantics
    recovery_targets: tuple[ActuatorTarget, ...] = Field(min_length=1)
    gestures: tuple[HeadGesturePolicy, ...] = Field(min_length=1)
    provenance: NonEmptyString

    @model_validator(mode="after")
    def validate_config(self) -> HeadGestureConfig:
        if len(self.affect_dimensions) != len(set(self.affect_dimensions)):
            raise ValueError("affect dimensions must be unique")
        names = tuple(target.actuator_name for target in self.recovery_targets)
        if names != self.semantics.actuator_names:
            raise ValueError(
                "recovery targets must use semantic yaw, tilt, pitch order"
            )
        kinds = tuple(policy.kind for policy in self.gestures)
        expected_kinds = (
            HeadGestureKind.NOD,
            HeadGestureKind.SHAKE,
            HeadGestureKind.TILT,
            HeadGestureKind.LOOK_UP,
            HeadGestureKind.LOOK_DOWN,
        )
        if kinds != expected_kinds:
            raise ValueError("head policies must contain every scheduled gesture once")
        for policy in self.gestures:
            if len(policy.affect_weights) != len(self.affect_dimensions):
                raise ValueError("gesture affect weights must match affect dimensions")
            if policy.actuator_name != self.semantics.actuator_for(policy.kind):
                raise ValueError("gesture policy violates semantic axis mapping")
        return self

    def policy(self, kind: HeadGestureKind) -> HeadGesturePolicy:
        """Return one configured scheduled-gesture policy."""

        for policy in self.gestures:
            if policy.kind is kind:
                return policy
        raise ValueError(f"head gesture kind is not scheduled: {kind.value!r}")


class HeadGestureScheduler:
    """Schedule at most one gesture per seeded, inspectable decision."""

    def __init__(
        self,
        *,
        config: HeadGestureConfig,
        controller_config: ControllerResponseConfig,
    ) -> None:
        if config.controller_response_model_id != controller_config.model_id:
            raise ValueError("head scheduler controller-response identity mismatch")
        if config.calibration_sha256 != controller_config.calibration_sha256:
            raise ValueError("head scheduler calibration identity mismatch")
        self._config = config
        self._model_sha256 = hashlib.sha256(
            config.model_dump_json().encode("utf-8")
        ).hexdigest()
        self._primitives = HeadPrimitiveGenerator(
            semantics=config.semantics,
            controller_config=controller_config,
            cadence_hz=config.cadence_hz,
        )
        for policy in config.gestures:
            self.validate_policy_response(policy)

    @property
    def config(self) -> HeadGestureConfig:
        return self._config

    @property
    def model_sha256(self) -> str:
        return self._model_sha256

    @property
    def primitives(self) -> HeadPrimitiveGenerator:
        return self._primitives

    def sample(
        self,
        intent: FilteredIntent,
        history: tuple[EventHistoryRecord, ...],
        rng: np.random.Generator,
        *,
        generated_monotonic_ns: int | None = None,
        accepted_pose: TargetUpdate | None = None,
    ) -> HeadGesture | None:
        """Capture a head pose and sample one bounded gesture from accepted history.

        Callers without a pose explicitly use the configured neutral pose.
        Streaming callers pass the complete accepted semantic head pose.
        """

        self._validate_intent(intent)
        if not isinstance(rng, np.random.Generator):
            raise TypeError("rng must be a NumPy Generator")
        pose = accepted_pose or TargetUpdate(
            offset_s=0.0, targets=self._config.recovery_targets
        )
        if (
            pose.offset_s != 0.0
            or tuple(target.actuator_name for target in pose.targets)
            != self._config.semantics.actuator_names
        ):
            raise ValueError(
                "accepted head pose must contain ordered semantic axes at zero"
            )
        gestures = self._head_history(history)
        self._validate_history(gestures)
        now_ns = (
            intent.accepted_monotonic_ns
            if generated_monotonic_ns is None
            else generated_monotonic_ns
        )
        if now_ns < intent.accepted_monotonic_ns:
            raise ValueError("generation time precedes accepted intent")
        if intent.support_status in {SupportStatus.FALLBACK, SupportStatus.STALE}:
            return None
        if self._inside_global_refractory(gestures, at_ns=now_ns):
            return None

        for policy in self._config.gestures:
            elapsed_since_end_s = self._elapsed_since_last_end(
                policy.kind,
                gestures,
                at_ns=now_ns,
            )
            hazard_hz = self.hazard_rate_hz(
                policy.kind,
                intent,
                elapsed_since_end_s=elapsed_since_end_s,
            )
            probability = -math.expm1(-hazard_hz * self._config.decision_interval_s)
            if float(rng.random()) < probability:
                candidate = self._new_gesture(
                    policy, rng, starts_ns=now_ns, accepted_pose=pose
                )
                try:
                    self._validate_history((*gestures, candidate))
                except ValueError:
                    return None
                return candidate
        return None

    def decision_due(
        self,
        history: tuple[EventHistoryRecord, ...],
        *,
        generated_monotonic_ns: int,
    ) -> bool:
        """Return whether the absolute scheduling cadence has elapsed."""

        decisions = [
            record.started_monotonic_ns
            for record in history
            if record.event_type == _DECISION_RECORD_TYPE
        ]
        if not decisions:
            return True
        interval_ns = round(self._config.decision_interval_s * _NANOSECONDS_PER_SECOND)
        return generated_monotonic_ns - max(decisions) >= interval_ns

    def record_decision(
        self,
        history: tuple[EventHistoryRecord, ...],
        *,
        generated_monotonic_ns: int,
    ) -> tuple[EventHistoryRecord, ...]:
        """Persist cadence even when a stochastic decision emits no gesture."""

        retained = tuple(
            record for record in history if record.event_type != _DECISION_RECORD_TYPE
        )
        return (
            *retained,
            EventHistoryRecord(
                event_type=_DECISION_RECORD_TYPE,
                started_monotonic_ns=generated_monotonic_ns,
                ended_monotonic_ns=generated_monotonic_ns,
                payload={"model_sha256": self._model_sha256},
            ),
        )

    def compact_history(
        self,
        history: tuple[EventHistoryRecord, ...],
        *,
        at_ns: int,
    ) -> tuple[EventHistoryRecord, ...]:
        """Bound completed gesture history without losing refractory state."""

        unrelated = tuple(
            record for record in history if record.event_type != "head-gesture/v1"
        )
        gestures = self._head_history(history)
        retained = [g for g in gestures if g.ends_monotonic_ns > at_ns]
        for kind in HeadGestureKind:
            if kind is HeadGestureKind.RETURN:
                continue
            completed = [
                g for g in gestures if g.kind is kind and g.ends_monotonic_ns <= at_ns
            ]
            if completed:
                retained.append(max(completed, key=lambda g: g.ends_monotonic_ns))
        unique = {gesture.gesture_id: gesture for gesture in retained}
        return (
            *unrelated,
            *(gesture.as_history_record() for gesture in unique.values()),
        )

    def record(
        self,
        history: tuple[EventHistoryRecord, ...],
        gesture: HeadGesture,
    ) -> tuple[EventHistoryRecord, ...]:
        """Append one head record without changing unrelated generic events."""

        gestures = self._head_history(history)
        self._validate_gesture_against_policy(gesture)
        for existing in gestures:
            if existing.gesture_id == gesture.gesture_id:
                if existing != gesture:
                    raise ValueError("head gesture ID maps to conflicting payloads")
                return history
        updated_gestures = (*gestures, gesture)
        self._validate_history(updated_gestures)
        return (*history, gesture.as_history_record())

    def hazard_rate_hz(
        self,
        kind: HeadGestureKind,
        intent: FilteredIntent,
        *,
        elapsed_since_end_s: float,
    ) -> float:
        """Return bounded affect- and recovery-conditioned gesture hazard."""

        if not math.isfinite(elapsed_since_end_s) and elapsed_since_end_s != math.inf:
            raise ValueError("elapsed gesture time must be finite or infinity")
        if elapsed_since_end_s < 0.0:
            raise ValueError("elapsed gesture time must be non-negative")
        self._validate_intent(intent)
        policy = self._config.policy(kind)
        if elapsed_since_end_s <= policy.refractory_s:
            return 0.0
        affect_log_scale = (
            sum(
                weight * coordinate
                for weight, coordinate in zip(
                    policy.affect_weights,
                    intent.vector,
                    strict=True,
                )
            )
            + policy.intensity_weight * intent.intensity
        )
        conditioned = policy.base_hazard_hz * math.exp(affect_log_scale)
        bounded = min(policy.max_hazard_hz, max(policy.min_hazard_hz, conditioned))
        if elapsed_since_end_s == math.inf:
            return bounded
        recovery = -math.expm1(
            -(elapsed_since_end_s - policy.refractory_s)
            / policy.recovery_time_constant_s
        )
        return bounded * recovery

    def validate_policy_response(self, policy: HeadGesturePolicy) -> None:
        """Reject any configured parameter extreme that can outrun its axis."""

        maximum_amplitude = policy.amplitude.maximum
        if policy.kind in {HeadGestureKind.NOD, HeadGestureKind.SHAKE}:
            available_s = policy.duration_s.minimum / (2 * policy.cycles.maximum)
            maximum_distance = 2.0 * maximum_amplitude
        else:
            available_s = policy.duration_s.minimum
            maximum_distance = maximum_amplitude
        minimum_s = self._primitives.minimum_transition_s(
            policy.actuator_name,
            maximum_distance,
        )
        if available_s < minimum_s:
            raise ValueError(
                f"{policy.kind.value} duration bounds are shorter than controller "
                "response"
            )
        recovery_minimum_s = self._primitives.minimum_transition_s(
            policy.actuator_name,
            maximum_amplitude,
        )
        if policy.recovery_s.minimum < recovery_minimum_s:
            raise ValueError(
                f"{policy.kind.value} recovery bounds are shorter than controller "
                "response"
            )

    def _validate_intent(self, intent: FilteredIntent) -> None:
        if intent.affect_schema_id != self._config.affect_schema_id:
            raise ValueError("head scheduler intent schema identity mismatch")
        if len(intent.vector) != len(self._config.affect_dimensions):
            raise ValueError("head scheduler intent dimension count mismatch")

    def _head_history(
        self,
        history: tuple[EventHistoryRecord, ...],
    ) -> tuple[HeadGesture, ...]:
        return tuple(
            HeadGesture.from_history_record(record)
            for record in history
            if record.event_type == "head-gesture/v1"
        )

    def _validate_history(self, gestures: tuple[HeadGesture, ...]) -> None:
        ids = [gesture.gesture_id for gesture in gestures]
        if len(ids) != len(set(ids)):
            raise ValueError("head gesture history IDs must be unique")
        ordered = sorted(
            gestures,
            key=lambda gesture: (gesture.starts_monotonic_ns, gesture.gesture_id),
        )
        for gesture in ordered:
            self._validate_gesture_against_policy(gesture)
        for left, right in zip(ordered, ordered[1:]):
            required_ns = round(
                self._config.global_refractory_s * _NANOSECONDS_PER_SECOND
            )
            if right.starts_monotonic_ns - left.ends_monotonic_ns < required_ns:
                raise ValueError("head gesture history violates global refractory")
        for kind in (
            HeadGestureKind.NOD,
            HeadGestureKind.SHAKE,
            HeadGestureKind.TILT,
            HeadGestureKind.LOOK_UP,
            HeadGestureKind.LOOK_DOWN,
        ):
            policy = self._config.policy(kind)
            same_kind = [gesture for gesture in ordered if gesture.kind is kind]
            for left, right in zip(same_kind, same_kind[1:]):
                required_ns = round(policy.refractory_s * _NANOSECONDS_PER_SECOND)
                if right.starts_monotonic_ns - left.ends_monotonic_ns < required_ns:
                    raise ValueError("head gesture history violates kind refractory")

    def _validate_gesture_against_policy(self, gesture: HeadGesture) -> None:
        if (
            gesture.model_id != self._config.model_id
            or gesture.model_sha256 != self._model_sha256
        ):
            raise ValueError("persisted head gesture model identity changed")
        policy = self._config.policy(gesture.kind)
        if gesture.actuator_name != policy.actuator_name:
            raise ValueError("persisted head gesture semantic axis changed")
        magnitude = abs(gesture.amplitude)
        if not policy.amplitude.minimum <= magnitude <= policy.amplitude.maximum:
            raise ValueError("persisted head gesture amplitude violates policy")
        bounded_parameters = (
            (gesture.duration_s, policy.duration_s, "duration"),
            (gesture.asymmetry, policy.asymmetry, "asymmetry"),
            (gesture.hold_s, policy.hold_s, "hold"),
            (gesture.recovery_s, policy.recovery_s, "recovery"),
        )
        for value, bounds, name in bounded_parameters:
            if not bounds.minimum <= value <= bounds.maximum:
                raise ValueError(f"persisted head gesture {name} violates policy")
        if not policy.cycles.minimum <= gesture.cycles <= policy.cycles.maximum:
            raise ValueError("persisted head gesture cycles violate policy")
        if policy.polarity == "positive" and gesture.amplitude < 0.0:
            raise ValueError("persisted head gesture polarity violates policy")
        if policy.polarity == "negative" and gesture.amplitude > 0.0:
            raise ValueError("persisted head gesture polarity violates policy")
        names = tuple(target.actuator_name for target in gesture.initial_targets)
        if names != self._config.semantics.actuator_names:
            raise ValueError(
                "persisted head gesture recovery is missing its accepted pose"
            )
        if gesture.recovery_targets != gesture.initial_targets:
            raise ValueError(
                "persisted head gesture recovery differs from accepted pose"
            )

    def _inside_global_refractory(
        self,
        history: tuple[HeadGesture, ...],
        *,
        at_ns: int,
    ) -> bool:
        if any(gesture.starts_monotonic_ns > at_ns for gesture in history):
            return True
        if any(
            gesture.starts_monotonic_ns <= at_ns < gesture.ends_monotonic_ns
            for gesture in history
        ):
            return True
        prior_ends = [
            gesture.ends_monotonic_ns
            for gesture in history
            if gesture.ends_monotonic_ns <= at_ns
        ]
        if not prior_ends:
            return False
        refractory_ns = round(
            self._config.global_refractory_s * _NANOSECONDS_PER_SECOND
        )
        return at_ns - max(prior_ends) <= refractory_ns

    @staticmethod
    def _elapsed_since_last_end(
        kind: HeadGestureKind,
        history: tuple[HeadGesture, ...],
        *,
        at_ns: int,
    ) -> float:
        prior_ends = [
            gesture.ends_monotonic_ns
            for gesture in history
            if gesture.kind is kind and gesture.ends_monotonic_ns <= at_ns
        ]
        if not prior_ends:
            return math.inf
        return (at_ns - max(prior_ends)) / _NANOSECONDS_PER_SECOND

    def _new_gesture(
        self,
        policy: HeadGesturePolicy,
        rng: np.random.Generator,
        *,
        starts_ns: int,
        accepted_pose: TargetUpdate,
    ) -> HeadGesture:
        amplitude = self._sample_float(policy.amplitude, rng)
        if policy.polarity == "negative":
            amplitude = -amplitude
        elif policy.polarity == "bidirectional" and float(rng.random()) < 0.5:
            amplitude = -amplitude
        duration_s = self._sample_float(policy.duration_s, rng)
        cycles = int(rng.integers(policy.cycles.minimum, policy.cycles.maximum + 1))
        asymmetry = self._sample_float(policy.asymmetry, rng)
        hold_s = self._sample_float(policy.hold_s, rng)
        recovery_s = self._sample_float(policy.recovery_s, rng)
        identity = "|".join(
            (
                self._model_sha256,
                policy.kind.value,
                str(starts_ns),
                repr(amplitude),
                repr(duration_s),
                str(cycles),
                repr(asymmetry),
                repr(hold_s),
                repr(recovery_s),
            )
        )
        gesture_id = (
            f"{policy.kind.value}-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
        )
        return HeadGesture(
            schema_version="head-gesture/v1",
            gesture_id=gesture_id,
            model_id=self._config.model_id,
            model_sha256=self._model_sha256,
            kind=policy.kind,
            starts_monotonic_ns=starts_ns,
            actuator_name=policy.actuator_name,
            amplitude=amplitude,
            duration_s=duration_s,
            cycles=cycles,
            asymmetry=asymmetry,
            hold_s=hold_s,
            recovery_s=recovery_s,
            recovery_targets=accepted_pose.targets,
            initial_targets=accepted_pose.targets,
        )

    @staticmethod
    def _sample_float(bounds: FloatRange, rng: np.random.Generator) -> float:
        if bounds.minimum == bounds.maximum:
            return bounds.minimum
        return float(rng.uniform(bounds.minimum, bounds.maximum))


def load_head_gesture_config(path: str | Path) -> HeadGestureConfig:
    """Load a configured scheduler prior without hardware access."""

    with Path(path).open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise ValueError("head gesture config root must be a mapping")
    return HeadGestureConfig.model_validate(document)
