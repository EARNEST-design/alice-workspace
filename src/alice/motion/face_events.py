"""Explicit, seeded blink and gaze events for stateful motion generation."""

from __future__ import annotations

import hashlib
import math
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.affect import MonotonicNanoseconds
from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.motion.controller_response import (
    ActuatorResponseParameters,
    ControllerResponseConfig,
)
from alice.motion.intent_filter import FilteredIntent, SupportStatus
from alice.motion.state import EventHistoryRecord, GeneratorState

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
FinitePositiveFloat = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
UnitIntervalFloat = Annotated[
    float,
    Field(ge=0.0, le=1.0, allow_inf_nan=False),
]
_EVENT_RECORD_TYPE = "face-event/v1"
_STATE_RECORD_TYPE = "face-event-state/v1"
_NANOSECONDS_PER_SECOND = 1_000_000_000


class FaceEventKind(StrEnum):
    """Inspectable sparse event families supported by the face policy."""

    BLINK = "blink"
    GAZE = "gaze"


class FaceEventPolicy(BaseModel):
    """Hazard, timing, coupling, and amplitude bounds for one event family."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: FaceEventKind
    actuator_names: tuple[NonEmptyString, NonEmptyString]
    base_hazard_hz: FinitePositiveFloat
    min_hazard_hz: FinitePositiveFloat
    max_hazard_hz: FinitePositiveFloat
    affect_weights: tuple[FiniteFloat, ...] = Field(min_length=1)
    intensity_weight: FiniteFloat
    refractory_s: FinitePositiveFloat
    recovery_time_constant_s: FinitePositiveFloat
    onset_s: FinitePositiveFloat
    hold_s: FinitePositiveFloat
    release_s: FinitePositiveFloat
    amplitude_min: FinitePositiveFloat
    amplitude_max: UnitIntervalFloat
    polarity: Literal["negative", "bidirectional"]
    conflicts_with: tuple[FaceEventKind, ...]
    provenance: NonEmptyString

    @model_validator(mode="after")
    def validate_policy(self) -> FaceEventPolicy:
        if len(set(self.actuator_names)) != 2:
            raise ValueError("face event actuator names must be distinct")
        if not self.min_hazard_hz <= self.base_hazard_hz <= self.max_hazard_hz:
            raise ValueError("base hazard must lie within configured hazard bounds")
        if self.amplitude_min > self.amplitude_max:
            raise ValueError("event amplitude minimum must not exceed maximum")
        expected_polarity = (
            "negative" if self.kind is FaceEventKind.BLINK else "bidirectional"
        )
        if self.polarity != expected_polarity:
            raise ValueError(
                f"{self.kind.value} events require {expected_polarity!r} polarity"
            )
        if self.kind not in self.conflicts_with:
            raise ValueError("an event policy must conflict with its own kind")
        if len(self.conflicts_with) != len(set(self.conflicts_with)):
            raise ValueError("event policy conflicts must be unique")
        return self


class FaceEventConfig(BaseModel):
    """Versioned statistical face-event policy bound to response identities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["face-event-config/v1"]
    model_id: NonEmptyString
    affect_schema_id: NonEmptyString
    affect_dimensions: tuple[NonEmptyString, ...] = Field(min_length=1)
    controller_response_model_id: NonEmptyString
    calibration_sha256: Sha256Hex
    controller_settings_sha256: Sha256Hex
    decision_interval_s: FinitePositiveFloat
    events: tuple[FaceEventPolicy, ...] = Field(min_length=1)
    provenance: NonEmptyString

    @model_validator(mode="after")
    def validate_config(self) -> FaceEventConfig:
        if len(self.affect_dimensions) != len(set(self.affect_dimensions)):
            raise ValueError("affect dimensions must be unique")
        kinds = [policy.kind for policy in self.events]
        if kinds != [FaceEventKind.BLINK, FaceEventKind.GAZE]:
            raise ValueError("face event policies must contain blink then gaze")
        if any(
            len(policy.affect_weights) != len(self.affect_dimensions)
            for policy in self.events
        ):
            raise ValueError("event affect weights must match affect dimensions")
        known_kinds = set(kinds)
        if any(not set(policy.conflicts_with) <= known_kinds for policy in self.events):
            raise ValueError("event conflict references an unknown event kind")
        for left in self.events:
            for right in left.conflicts_with:
                opposite = next(
                    policy for policy in self.events if policy.kind is right
                )
                if left.kind not in opposite.conflicts_with:
                    raise ValueError("event conflicts must be symmetric")
        return self

    def policy(self, kind: FaceEventKind) -> FaceEventPolicy:
        """Return one event policy in stable configuration order."""

        for policy in self.events:
            if policy.kind is kind:
                return policy
        raise ValueError(f"unknown face event kind: {kind!r}")


class FaceEvent(BaseModel):
    """One absolute, inspectable event with a coupled sparse peak target."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["face-event/v1"]
    event_id: NonEmptyString
    model_id: NonEmptyString
    model_sha256: Sha256Hex
    kind: FaceEventKind
    starts_monotonic_ns: MonotonicNanoseconds
    onset_s: FinitePositiveFloat
    hold_s: FinitePositiveFloat
    release_s: FinitePositiveFloat
    amplitude: Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
    actuator_names: tuple[NonEmptyString, NonEmptyString]
    peak_targets: tuple[ActuatorTarget, ActuatorTarget]

    @model_validator(mode="after")
    def validate_coupled_targets(self) -> FaceEvent:
        if self.amplitude == 0.0:
            raise ValueError("face event amplitude must be non-zero")
        if len(set(self.actuator_names)) != 2:
            raise ValueError("face event actuator names must be distinct")
        target_names = tuple(target.actuator_name for target in self.peak_targets)
        if target_names != self.actuator_names:
            raise ValueError("peak targets must match ordered event actuator names")
        target_values = {target.normalized_position for target in self.peak_targets}
        if len(target_values) != 1:
            raise ValueError("coupled face event targets must have equal values")
        expected_value = (
            -self.amplitude if self.kind is FaceEventKind.BLINK else self.amplitude
        )
        if self.kind is FaceEventKind.BLINK and self.amplitude < 0.0:
            raise ValueError("blink amplitude must be positive")
        if next(iter(target_values)) != expected_value:
            raise ValueError("coupled peak targets must encode the event amplitude")
        return self

    @property
    def ends_monotonic_ns(self) -> int:
        """Return the absolute exclusive end of the release phase."""

        duration_ns = round(
            (self.onset_s + self.hold_s + self.release_s) * _NANOSECONDS_PER_SECOND
        )
        return self.starts_monotonic_ns + duration_ns

    def as_history_record(self) -> EventHistoryRecord:
        """Encode this typed event in the generic generator-state envelope."""

        payload = self.model_dump(mode="json")
        return EventHistoryRecord(
            event_type=_EVENT_RECORD_TYPE,
            started_monotonic_ns=self.starts_monotonic_ns,
            ended_monotonic_ns=self.ends_monotonic_ns,
            payload={str(key): value for key, value in payload.items()},
        )

    @classmethod
    def from_history_record(cls, record: EventHistoryRecord) -> FaceEvent:
        """Decode an event and verify its duplicated absolute interval."""

        if record.event_type != _EVENT_RECORD_TYPE:
            raise ValueError("history record is not a face event")
        event = cls.model_validate(record.payload)
        if (
            event.starts_monotonic_ns != record.started_monotonic_ns
            or event.ends_monotonic_ns != record.ended_monotonic_ns
        ):
            raise ValueError("face event history interval does not match payload")
        return event


class FaceEventState(BaseModel):
    """Typed view of absolute face-event history and planned horizon coverage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["face-event-state/v1"]
    model_id: NonEmptyString
    model_sha256: Sha256Hex
    monotonic_ns: MonotonicNanoseconds
    planned_through_ns: MonotonicNanoseconds
    history: tuple[FaceEvent, ...] = ()

    @model_validator(mode="after")
    def validate_state(self) -> FaceEventState:
        if self.planned_through_ns < self.monotonic_ns:
            raise ValueError("face event plan cannot end before its state boundary")
        ids = [event.event_id for event in self.history]
        if len(ids) != len(set(ids)):
            raise ValueError("face event history IDs must be unique")
        if any(
            event.model_id != self.model_id or event.model_sha256 != self.model_sha256
            for event in self.history
        ):
            raise ValueError("face event history model identity changed")
        if any(
            event.starts_monotonic_ns > self.planned_through_ns
            for event in self.history
        ):
            raise ValueError("face event begins beyond planned horizon coverage")
        return self

    def cancel_future(self) -> FaceEventState:
        """Discard uncommitted events and reopen planning at the accepted boundary."""

        return self.model_copy(
            update={
                "history": tuple(
                    event
                    for event in self.history
                    if event.starts_monotonic_ns <= self.monotonic_ns
                ),
                "planned_through_ns": self.monotonic_ns,
            }
        )

    def advance(
        self,
        events: tuple[FaceEvent, ...],
        *,
        monotonic_ns: int,
        planned_through_ns: int,
    ) -> FaceEventState:
        """Merge sampled events while moving the accepted absolute boundary."""

        if monotonic_ns < self.monotonic_ns:
            raise ValueError("face event state time must not move backward")
        if planned_through_ns < self.planned_through_ns:
            raise ValueError("face event planned horizon must not move backward")
        by_id = {event.event_id: event for event in self.history}
        for event in events:
            previous = by_id.get(event.event_id)
            if previous is not None and previous != event:
                raise ValueError("face event ID maps to conflicting payloads")
            by_id[event.event_id] = event
        history = tuple(
            sorted(
                by_id.values(),
                key=lambda event: (event.starts_monotonic_ns, event.event_id),
            )
        )
        return FaceEventState(
            schema_version=self.schema_version,
            model_id=self.model_id,
            model_sha256=self.model_sha256,
            monotonic_ns=monotonic_ns,
            planned_through_ns=planned_through_ns,
            history=history,
        )

    def to_generator_state(self, state: GeneratorState) -> GeneratorState:
        """Persist typed state without synthesizing any other continuation field."""

        if state.monotonic_ns != self.monotonic_ns:
            raise ValueError("generator state must already be at face event boundary")
        metadata: dict[str, JsonValue] = {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "model_sha256": self.model_sha256,
            "planned_through_ns": self.planned_through_ns,
        }
        retained = tuple(
            record
            for record in state.event_history
            if record.event_type not in {_EVENT_RECORD_TYPE, _STATE_RECORD_TYPE}
        )
        records = tuple(event.as_history_record() for event in self.history)
        state_record = EventHistoryRecord(
            event_type=_STATE_RECORD_TYPE,
            started_monotonic_ns=self.monotonic_ns,
            ended_monotonic_ns=self.planned_through_ns,
            payload=metadata,
        )
        return state.model_copy(
            update={"event_history": (*retained, *records, state_record)}
        )

    @classmethod
    def from_generator_state(
        cls,
        state: GeneratorState,
        *,
        model_id: str,
        model_sha256: str,
    ) -> FaceEventState:
        """Restore typed history from generic records, or initialize it empty."""

        event_records = tuple(
            record
            for record in state.event_history
            if record.event_type == _EVENT_RECORD_TYPE
        )
        metadata_records = tuple(
            record
            for record in state.event_history
            if record.event_type == _STATE_RECORD_TYPE
        )
        if len(metadata_records) > 1:
            raise ValueError("generator state contains multiple face event states")
        history = tuple(
            FaceEvent.from_history_record(record) for record in event_records
        )
        if not metadata_records:
            if history:
                raise ValueError("face event history is missing typed state metadata")
            return cls(
                schema_version="face-event-state/v1",
                model_id=model_id,
                model_sha256=model_sha256,
                monotonic_ns=state.monotonic_ns,
                planned_through_ns=state.monotonic_ns,
                history=(),
            )

        record = metadata_records[0]
        payload = dict(record.payload)
        restored = cls.model_validate(
            {
                "schema_version": payload.get("schema_version"),
                "model_id": payload.get("model_id"),
                "model_sha256": payload.get("model_sha256"),
                "monotonic_ns": record.started_monotonic_ns,
                "planned_through_ns": payload.get("planned_through_ns"),
                "history": history,
            }
        )
        if record.ended_monotonic_ns != restored.planned_through_ns:
            raise ValueError("face event state interval does not match payload")
        if restored.monotonic_ns != state.monotonic_ns:
            raise ValueError("face event state does not match generator boundary")
        if restored.model_id != model_id or restored.model_sha256 != model_sha256:
            raise ValueError("persisted face event model identity changed")
        return restored


class FaceEventGenerator:
    """Sample absolute blink and gaze events from affect-conditioned hazards."""

    def __init__(
        self,
        *,
        config: FaceEventConfig,
        controller_config: ControllerResponseConfig,
    ) -> None:
        if config.controller_response_model_id != controller_config.model_id:
            raise ValueError("face event controller-response model identity mismatch")
        if config.calibration_sha256 != controller_config.calibration_sha256:
            raise ValueError("face event calibration identity mismatch")
        self._config = config
        self._controller_config = controller_config
        self._model_sha256 = hashlib.sha256(
            config.model_dump_json().encode("utf-8")
        ).hexdigest()
        known_actuators = {
            parameters.actuator_name for parameters in controller_config.actuators
        }
        configured_actuators = {
            actuator for policy in config.events for actuator in policy.actuator_names
        }
        if not configured_actuators <= known_actuators:
            raise ValueError("face events reference unknown semantic actuators")
        for policy in config.events:
            minimum_s = self.minimum_transition_s(
                policy.actuator_names,
                policy.amplitude_max,
            )
            if policy.onset_s < minimum_s or policy.release_s < minimum_s:
                raise ValueError(
                    f"{policy.kind.value} phase is shorter than controller response"
                )

    @property
    def config(self) -> FaceEventConfig:
        return self._config

    @property
    def model_sha256(self) -> str:
        return self._model_sha256

    @property
    def blink_refractory_s(self) -> float:
        return self._config.policy(FaceEventKind.BLINK).refractory_s

    def state_from(self, state: GeneratorState) -> FaceEventState:
        """Create the typed face-event view of a generic continuation state."""

        if state.calibration_sha256 != self._config.calibration_sha256:
            raise ValueError("generator state calibration identity mismatch")
        if state.controller_settings_sha256 != self._config.controller_settings_sha256:
            raise ValueError("generator state controller identity mismatch")
        restored = FaceEventState.from_generator_state(
            state,
            model_id=self._config.model_id,
            model_sha256=self._model_sha256,
        )
        self._validate_history_rules(restored.history)
        return restored

    def compact_state(self, state: FaceEventState) -> FaceEventState:
        """Keep active/planned events and the last completed event per kind."""

        retained: list[FaceEvent] = [
            event
            for event in state.history
            if event.ends_monotonic_ns > state.monotonic_ns
        ]
        for kind in FaceEventKind:
            completed = [
                event
                for event in state.history
                if event.kind is kind and event.ends_monotonic_ns <= state.monotonic_ns
            ]
            if completed:
                retained.append(
                    max(completed, key=lambda event: event.ends_monotonic_ns)
                )
        return state.model_copy(
            update={
                "history": tuple(
                    sorted(
                        {event.event_id: event for event in retained}.values(),
                        key=lambda event: (event.starts_monotonic_ns, event.event_id),
                    )
                )
            }
        )

    def sample(
        self,
        intent: FilteredIntent,
        state: FaceEventState | GeneratorState,
        rng: np.random.Generator,
        horizon_s: float,
    ) -> tuple[FaceEvent, ...]:
        """Sample overlapping events, or return only active events when unsupported.

        Conservative callers persist cancellation with ``state.cancel_future()``
        before advancing the accepted boundary; sampling never mutates its input.
        """

        typed_state = (
            self.state_from(state) if isinstance(state, GeneratorState) else state
        )
        self._validate_inputs(intent, typed_state, rng=rng, horizon_s=horizon_s)
        if intent.support_status in {SupportStatus.STALE, SupportStatus.FALLBACK}:
            return tuple(
                event
                for event in typed_state.history
                if event.starts_monotonic_ns
                <= typed_state.monotonic_ns
                < event.ends_monotonic_ns
            )
        horizon_ns = round(horizon_s * _NANOSECONDS_PER_SECOND)
        horizon_end_ns = typed_state.monotonic_ns + horizon_ns
        existing = tuple(
            event
            for event in typed_state.history
            if event.ends_monotonic_ns > typed_state.monotonic_ns
            and event.starts_monotonic_ns <= horizon_end_ns
        )
        if typed_state.planned_through_ns >= horizon_end_ns:
            return existing

        history = list(typed_state.history)
        interval_ns = round(self._config.decision_interval_s * _NANOSECONDS_PER_SECOND)
        candidate_ns = (typed_state.planned_through_ns // interval_ns + 1) * interval_ns
        while candidate_ns <= horizon_end_ns:
            for policy in self._config.events:
                elapsed_since_end_s = self._elapsed_since_last_end(
                    policy.kind,
                    history,
                    at_ns=candidate_ns,
                )
                if self._time_conflicts(policy, history, starts_ns=candidate_ns):
                    continue
                hazard_hz = self.hazard_rate_hz(
                    policy.kind,
                    intent,
                    elapsed_since_end_s=elapsed_since_end_s,
                )
                probability = -math.expm1(-hazard_hz * self._config.decision_interval_s)
                if float(rng.random()) < probability:
                    history.append(self._new_event(policy, rng, candidate_ns))
            candidate_ns += interval_ns

        sampled = tuple(
            event
            for event in history
            if event.ends_monotonic_ns > typed_state.monotonic_ns
            and event.starts_monotonic_ns <= horizon_end_ns
        )
        return tuple(
            sorted(
                sampled, key=lambda event: (event.starts_monotonic_ns, event.event_id)
            )
        )

    def hazard_rate_hz(
        self,
        kind: FaceEventKind,
        intent: FilteredIntent,
        *,
        elapsed_since_end_s: float,
    ) -> float:
        """Return the inspectable affect- and recovery-conditioned event rate."""

        if not math.isfinite(elapsed_since_end_s) and elapsed_since_end_s != math.inf:
            raise ValueError("elapsed event time must be finite or positive infinity")
        if elapsed_since_end_s < 0.0:
            raise ValueError("elapsed event time must be non-negative")
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

    def minimum_transition_s(
        self,
        actuator_names: tuple[str, str],
        amplitude: float,
    ) -> float:
        """Return the slowest rest-to-rest response for a coupled sparse target."""

        if not math.isfinite(amplitude) or not 0.0 < amplitude <= 1.0:
            raise ValueError("event amplitude must be finite and in (0, 1]")
        return max(
            self._minimum_rest_to_rest_s(
                amplitude,
                self._controller_config.actuator(actuator_name),
            )
            for actuator_name in actuator_names
        )

    def _validate_inputs(
        self,
        intent: FilteredIntent,
        state: FaceEventState,
        *,
        rng: np.random.Generator,
        horizon_s: float,
    ) -> None:
        self._validate_intent(intent)
        if not isinstance(rng, np.random.Generator):
            raise TypeError("rng must be a NumPy Generator")
        if not math.isfinite(horizon_s) or horizon_s <= 0.0:
            raise ValueError("horizon_s must be finite and positive")
        if round(horizon_s * _NANOSECONDS_PER_SECOND) <= 0:
            raise ValueError("horizon_s must cover at least one nanosecond")
        if state.model_id != self._config.model_id:
            raise ValueError("face event state model identity mismatch")
        if state.model_sha256 != self._model_sha256:
            raise ValueError("face event state model checksum mismatch")
        if intent.accepted_monotonic_ns > state.monotonic_ns:
            raise ValueError("filtered intent postdates face event state")
        self._validate_history_rules(state.history)

    def _validate_intent(self, intent: FilteredIntent) -> None:
        if intent.affect_schema_id != self._config.affect_schema_id:
            raise ValueError("face event intent schema identity mismatch")
        if len(intent.vector) != len(self._config.affect_dimensions):
            raise ValueError("face event intent dimension count mismatch")

    def _validate_history_rules(self, history: tuple[FaceEvent, ...]) -> None:
        ordered = sorted(history, key=lambda event: event.starts_monotonic_ns)
        for index, left in enumerate(ordered):
            left_policy = self._config.policy(left.kind)
            self._validate_event_against_policy(left, left_policy)
            for right in ordered[index + 1 :]:
                if right.starts_monotonic_ns >= left.ends_monotonic_ns:
                    break
                if right.kind in left_policy.conflicts_with or set(
                    left.actuator_names
                ) & set(right.actuator_names):
                    raise ValueError("conflicting face events overlap")
        for kind in FaceEventKind:
            policy = self._config.policy(kind)
            same_kind = [event for event in ordered if event.kind is kind]
            for left, right in zip(same_kind, same_kind[1:]):
                gap_ns = right.starts_monotonic_ns - left.ends_monotonic_ns
                if gap_ns < round(policy.refractory_s * _NANOSECONDS_PER_SECOND):
                    raise ValueError("persisted face event violates refractory period")

    def _validate_event_against_policy(
        self,
        event: FaceEvent,
        policy: FaceEventPolicy,
    ) -> None:
        if event.actuator_names != policy.actuator_names:
            raise ValueError("persisted face event coupling changed")
        magnitude = abs(event.amplitude)
        if not policy.amplitude_min <= magnitude <= policy.amplitude_max:
            raise ValueError("persisted face event amplitude violates current policy")
        if policy.polarity == "negative" and event.amplitude < 0.0:
            raise ValueError("persisted face event polarity violates current policy")
        minimum_s = self.minimum_transition_s(event.actuator_names, magnitude)
        if event.onset_s < minimum_s or event.release_s < minimum_s:
            raise ValueError(
                "persisted face event phase is shorter than controller response"
            )
        if (
            event.onset_s != policy.onset_s
            or event.hold_s != policy.hold_s
            or event.release_s != policy.release_s
        ):
            raise ValueError("persisted face event timing violates current policy")

    def _time_conflicts(
        self,
        policy: FaceEventPolicy,
        history: list[FaceEvent],
        *,
        starts_ns: int,
    ) -> bool:
        ends_ns = starts_ns + round(
            (policy.onset_s + policy.hold_s + policy.release_s)
            * _NANOSECONDS_PER_SECOND
        )
        return any(
            starts_ns < event.ends_monotonic_ns
            and event.starts_monotonic_ns < ends_ns
            and (
                event.kind in policy.conflicts_with
                or bool(set(event.actuator_names) & set(policy.actuator_names))
            )
            for event in history
        )

    @staticmethod
    def _elapsed_since_last_end(
        kind: FaceEventKind,
        history: list[FaceEvent],
        *,
        at_ns: int,
    ) -> float:
        prior_ends = [
            event.ends_monotonic_ns
            for event in history
            if event.kind is kind and event.ends_monotonic_ns <= at_ns
        ]
        if not prior_ends:
            return math.inf
        return (at_ns - max(prior_ends)) / _NANOSECONDS_PER_SECOND

    def _new_event(
        self,
        policy: FaceEventPolicy,
        rng: np.random.Generator,
        starts_ns: int,
    ) -> FaceEvent:
        magnitude = float(rng.uniform(policy.amplitude_min, policy.amplitude_max))
        amplitude = magnitude
        if policy.polarity == "bidirectional" and float(rng.random()) < 0.5:
            amplitude = -magnitude
        peak_value = -amplitude if policy.kind is FaceEventKind.BLINK else amplitude
        identity = "|".join(
            (
                self._model_sha256,
                policy.kind.value,
                str(starts_ns),
                repr(amplitude),
            )
        )
        event_id = (
            f"{policy.kind.value}-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
        )
        peak_targets = (
            ActuatorTarget(
                actuator_name=policy.actuator_names[0],
                normalized_position=peak_value,
            ),
            ActuatorTarget(
                actuator_name=policy.actuator_names[1],
                normalized_position=peak_value,
            ),
        )
        return FaceEvent(
            schema_version="face-event/v1",
            event_id=event_id,
            model_id=self._config.model_id,
            model_sha256=self._model_sha256,
            kind=policy.kind,
            starts_monotonic_ns=starts_ns,
            onset_s=policy.onset_s,
            hold_s=policy.hold_s,
            release_s=policy.release_s,
            amplitude=amplitude,
            actuator_names=policy.actuator_names,
            peak_targets=peak_targets,
        )

    @staticmethod
    def _minimum_rest_to_rest_s(
        distance: float,
        parameters: ActuatorResponseParameters,
    ) -> float:
        acceleration = parameters.max_acceleration_per_s2
        max_speed = parameters.max_velocity_per_s
        triangular_limit = max_speed**2 / acceleration
        if distance <= triangular_limit:
            return 2.0 * math.sqrt(distance / acceleration)
        return distance / max_speed + max_speed / acceleration


def load_face_event_config(path: str | Path) -> FaceEventConfig:
    """Load and validate a face-event policy without hardware access."""

    with Path(path).open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise ValueError("face event config root must be a mapping")
    return FaceEventConfig.model_validate(document)
