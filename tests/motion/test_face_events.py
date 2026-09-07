"""Behavioral tests for explicit stateful blink and gaze events."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.motion.anchors import load_procedural_motion_config
from alice.motion.controller_response import load_controller_response_config
from alice.motion.face_events import (
    FaceEvent,
    FaceEventGenerator,
    FaceEventKind,
    load_face_event_config,
)
from alice.motion.intent_filter import FilteredIntent, SupportStatus
from alice.motion.state import (
    ActuatorVelocity,
    EventHistoryRecord,
    GeneratorState,
    dump_state,
    load_state,
)

ROOT = Path(__file__).parents[2]
CONFIG_PATH = ROOT / "config" / "models" / "face-events-v1.yaml"
RESPONSE_PATH = ROOT / "config" / "models" / "maestro-response-v1.yaml"
PROCEDURAL_PATH = ROOT / "config" / "models" / "procedural-motion-v1.yaml"


def _intent(
    *,
    vector: tuple[float, float, float] = (0.0, 0.0, 0.0),
    intensity: float = 0.7,
    accepted_ns: int = 0,
) -> FilteredIntent:
    return FilteredIntent(
        schema_version="filtered-intent/v1",
        affect_schema_id="affect-vector/v1",
        vector=vector,
        intensity=intensity,
        source_id="face-event-test",
        accepted_monotonic_ns=accepted_ns,
        support_status=SupportStatus.SUPPORTED,
        support_distance=0.0,
        reason="deterministic face-event fixture",
    )


def _generator() -> FaceEventGenerator:
    return FaceEventGenerator(
        config=load_face_event_config(CONFIG_PATH),
        controller_config=load_controller_response_config(RESPONSE_PATH),
    )


def _generic_state(*, now_ns: int = 0) -> GeneratorState:
    procedural = load_procedural_motion_config(PROCEDURAL_PATH)
    target = TargetUpdate(
        offset_s=0.0,
        targets=procedural.anchor("neutral").targets,
    )
    rng = np.random.default_rng(31)
    return GeneratorState(
        schema_version="generator-state/v1",
        last_accepted_target=target,
        last_reported_pose=target,
        estimated_velocity=tuple(
            ActuatorVelocity(actuator_name=item.actuator_name, velocity_per_s=0.0)
            for item in target.targets
        ),
        filtered_intent=_intent(accepted_ns=now_ns),
        latent_vector=(0.0,),
        numpy_rng_state=rng.bit_generator.state,
        torch_rng_state=(1,),
        event_history=(),
        model_id=procedural.model_id,
        model_sha256=hashlib.sha256(
            procedural.model_dump_json().encode("utf-8")
        ).hexdigest(),
        calibration_sha256=procedural.calibration_sha256,
        controller_settings_sha256=procedural.controller_settings_sha256,
        monotonic_ns=now_ns,
    )


def _sample_many(*, seconds: int, seed: int) -> tuple[FaceEvent, ...]:
    generator = _generator()
    rng = np.random.default_rng(seed)
    generic = _generic_state()
    state = generator.state_from(generic)
    observed: dict[str, FaceEvent] = {}
    for second in range(seconds):
        events = generator.sample(
            _intent(accepted_ns=second * 1_000_000_000), state, rng, 1.0
        )
        observed.update((event.event_id, event) for event in events)
        boundary_ns = (second + 1) * 1_000_000_000
        state = state.advance(
            events,
            monotonic_ns=boundary_ns,
            planned_through_ns=boundary_ns,
        )
    return tuple(sorted(observed.values(), key=lambda event: event.starts_monotonic_ns))


def _sample_partitions(
    durations_s: tuple[float, ...],
    *,
    seed: int,
) -> tuple[FaceEvent, ...]:
    generator = _generator()
    rng = np.random.default_rng(seed)
    state = generator.state_from(_generic_state())
    observed: dict[str, FaceEvent] = {}
    boundary_ns = 0
    for duration_s in durations_s:
        events = generator.sample(
            _intent(accepted_ns=boundary_ns),
            state,
            rng,
            duration_s,
        )
        observed.update((event.event_id, event) for event in events)
        boundary_ns += round(duration_s * 1_000_000_000)
        state = state.advance(
            events,
            monotonic_ns=boundary_ns,
            planned_through_ns=boundary_ns,
        )
    return tuple(
        sorted(
            observed.values(),
            key=lambda event: (event.starts_monotonic_ns, event.event_id),
        )
    )


def _blink(
    generator: FaceEventGenerator,
    *,
    onset_s: float = 0.6,
    hold_s: float = 0.2,
    release_s: float = 0.6,
    amplitude: float = 0.5,
) -> FaceEvent:
    return FaceEvent(
        schema_version="face-event/v1",
        event_id="persisted-blink",
        model_id=generator.config.model_id,
        model_sha256=generator.model_sha256,
        kind=FaceEventKind.BLINK,
        starts_monotonic_ns=1_000_000_000,
        onset_s=onset_s,
        hold_s=hold_s,
        release_s=release_s,
        amplitude=amplitude,
        actuator_names=("lower_eyelids", "upper_eyelids"),
        peak_targets=(
            ActuatorTarget(
                actuator_name="lower_eyelids",
                normalized_position=-amplitude,
            ),
            ActuatorTarget(
                actuator_name="upper_eyelids",
                normalized_position=-amplitude,
            ),
        ),
    )


def _persisted(generator: FaceEventGenerator, event: FaceEvent) -> GeneratorState:
    generic = _generic_state()
    typed = generator.state_from(generic).advance(
        (event,),
        monotonic_ns=0,
        planned_through_ns=event.starts_monotonic_ns,
    )
    return typed.to_generator_state(generic)


def test_blinks_respect_refractory_period() -> None:
    """Forgetting absolute history would permit rapid blinks at horizon seams."""

    generator = _generator()
    blinks = [
        event
        for event in _sample_many(seconds=60, seed=3)
        if event.kind is FaceEventKind.BLINK
    ]

    assert len(blinks) >= 2
    assert min(
        right.starts_monotonic_ns - left.ends_monotonic_ns
        for left, right in zip(blinks, blinks[1:])
    ) >= round(generator.blink_refractory_s * 1_000_000_000)


def test_events_are_seeded_but_not_static() -> None:
    """Ambient RNG use or ignored seeds would destroy replay or diversity."""

    assert _sample_many(seconds=30, seed=4) == _sample_many(seconds=30, seed=4)
    assert _sample_many(seconds=30, seed=4) != _sample_many(seconds=30, seed=5)


def test_decisions_are_invariant_to_irregular_horizon_partitions() -> None:
    """Restarting cadence at each 0.7-second seam would shift event decisions."""

    one_shot = _sample_partitions((7.0,), seed=23)
    partitioned = _sample_partitions((0.7,) * 10, seed=23)

    assert partitioned == one_shot


def test_sub_interval_horizons_accumulate_to_event_decisions() -> None:
    """Repeated horizons below decision cadence must not suppress all events."""

    one_shot = _sample_partitions((0.5,), seed=3)
    partitioned = _sample_partitions((0.05,) * 10, seed=3)

    assert one_shot
    assert partitioned == one_shot


def test_hazard_depends_on_continuous_affect_and_elapsed_refractory() -> None:
    """A constant timer would ignore both requested affect and recovery state."""

    generator = _generator()
    neutral = generator.hazard_rate_hz(
        FaceEventKind.BLINK,
        _intent(vector=(0.0, 0.0, 0.0), intensity=0.0),
        elapsed_since_end_s=10.0,
    )
    aroused = generator.hazard_rate_hz(
        FaceEventKind.BLINK,
        _intent(vector=(0.0, 1.0, 0.0), intensity=1.0),
        elapsed_since_end_s=10.0,
    )

    assert (
        generator.hazard_rate_hz(
            FaceEventKind.BLINK,
            _intent(),
            elapsed_since_end_s=generator.blink_refractory_s,
        )
        == 0.0
    )
    assert aroused > neutral > 0.0


def test_events_use_coupled_sparse_semantic_targets() -> None:
    """Independent physical-eye values could create divergent gaze or eyelids."""

    events = _sample_many(seconds=30, seed=7)

    assert {event.kind for event in events} == {
        FaceEventKind.BLINK,
        FaceEventKind.GAZE,
    }
    for event in events:
        expected_names = (
            ("lower_eyelids", "upper_eyelids")
            if event.kind is FaceEventKind.BLINK
            else ("right_eye_horizontal", "left_eye_horizontal")
        )
        assert event.actuator_names == expected_names
        assert (
            tuple(target.actuator_name for target in event.peak_targets)
            == expected_names
        )
        assert len({target.normalized_position for target in event.peak_targets}) == 1


def test_checked_in_timings_cover_controller_rest_to_rest_response() -> None:
    """An event phase shorter than controller travel cannot realize its target."""

    generator = _generator()
    for policy in generator.config.events:
        minimum_s = generator.minimum_transition_s(
            policy.actuator_names,
            policy.amplitude_max,
        )
        assert policy.onset_s >= minimum_s
        assert policy.release_s >= minimum_s


def test_face_event_state_round_trips_through_generic_generator_state() -> None:
    """Subtype-only state would be lost at the generic streaming boundary."""

    generator = _generator()
    head_record = EventHistoryRecord(
        event_type="head-gesture/v1",
        started_monotonic_ns=8_000_000_000,
        ended_monotonic_ns=9_000_000_000,
        payload={"kind": "nod", "amplitude": 0.2},
    )
    generic = _generic_state().model_copy(update={"event_history": (head_record,)})
    state = generator.state_from(generic)
    events = generator.sample(_intent(), state, np.random.default_rng(11), 5.0)
    state = state.advance(
        events,
        monotonic_ns=2_000_000_000,
        planned_through_ns=5_000_000_000,
    )
    boundary = generic.model_copy(update={"monotonic_ns": 2_000_000_000})

    persisted = state.to_generator_state(boundary)
    restored = generator.state_from(load_state(dump_state(persisted)))

    assert restored == state
    assert all(record.started_monotonic_ns >= 0 for record in persisted.event_history)
    assert tuple(
        record
        for record in persisted.event_history
        if record.event_type == "head-gesture/v1"
    ) == (head_record,)


def test_overlapping_horizon_reuses_persisted_future_events() -> None:
    """Resampling the overlap would move already planned absolute events."""

    generator = _generator()
    rng = np.random.default_rng(19)
    initial = generator.state_from(_generic_state())
    first = generator.sample(_intent(), initial, rng, 4.0)
    boundary = initial.advance(
        first,
        monotonic_ns=2_000_000_000,
        planned_through_ns=4_000_000_000,
    )
    overlap = generator.sample(_intent(accepted_ns=2_000_000_000), boundary, rng, 2.0)

    assert overlap == tuple(
        event
        for event in first
        if event.ends_monotonic_ns > 2_000_000_000
        and event.starts_monotonic_ns <= 4_000_000_000
    )


def test_generator_rejects_conflicting_persisted_events() -> None:
    """Accepting overlapping eye policies would make sparse ownership ambiguous."""

    generator = _generator()
    base = FaceEvent(
        schema_version="face-event/v1",
        event_id="blink-conflict",
        model_id=generator.config.model_id,
        model_sha256=generator.model_sha256,
        kind=FaceEventKind.BLINK,
        starts_monotonic_ns=1_000_000_000,
        onset_s=0.6,
        hold_s=0.2,
        release_s=0.6,
        amplitude=0.5,
        actuator_names=("lower_eyelids", "upper_eyelids"),
        peak_targets=(
            ActuatorTarget(actuator_name="lower_eyelids", normalized_position=-0.5),
            ActuatorTarget(actuator_name="upper_eyelids", normalized_position=-0.5),
        ),
    )
    gaze = FaceEvent(
        schema_version="face-event/v1",
        event_id="gaze-conflict",
        model_id=generator.config.model_id,
        model_sha256=generator.model_sha256,
        kind=FaceEventKind.GAZE,
        starts_monotonic_ns=1_100_000_000,
        onset_s=0.4,
        hold_s=0.8,
        release_s=0.4,
        amplitude=0.1,
        actuator_names=("right_eye_horizontal", "left_eye_horizontal"),
        peak_targets=(
            ActuatorTarget(
                actuator_name="right_eye_horizontal", normalized_position=0.1
            ),
            ActuatorTarget(
                actuator_name="left_eye_horizontal", normalized_position=0.1
            ),
        ),
    )
    state = generator.state_from(_generic_state()).advance(
        (base, gaze),
        monotonic_ns=0,
        planned_through_ns=2_000_000_000,
    )

    with pytest.raises(ValueError, match="conflicting face events"):
        generator.sample(_intent(), state, np.random.default_rng(2), 2.0)


def test_restore_rejects_persisted_event_outside_policy_amplitude() -> None:
    """A forged current model hash must not admit an old amplitude policy."""

    generator = _generator()
    persisted = _persisted(generator, _blink(generator, amplitude=0.9))

    with pytest.raises(ValueError, match="amplitude.*policy"):
        generator.state_from(persisted)


def test_restore_rejects_response_infeasible_persisted_event() -> None:
    """A 0.01-second transition cannot realize a 0.9 eyelid movement."""

    generator = _generator()
    persisted = _persisted(
        generator,
        _blink(generator, onset_s=0.01, release_s=0.01, amplitude=0.9),
    )

    with pytest.raises(ValueError, match="amplitude.*policy|controller response"):
        generator.state_from(persisted)


def test_restore_checks_each_event_against_controller_response() -> None:
    """Policy-shaped amplitude alone must not bypass response feasibility."""

    generator = _generator()
    persisted = _persisted(
        generator,
        _blink(generator, onset_s=0.01, release_s=0.01, amplitude=0.49),
    )

    with pytest.raises(ValueError, match="controller response"):
        generator.state_from(persisted)


def test_restore_rejects_event_from_changed_phase_policy() -> None:
    """Claiming the current hash must not hide a changed hold-duration policy."""

    generator = _generator()
    persisted = _persisted(generator, _blink(generator, hold_s=0.4))

    with pytest.raises(ValueError, match="timing.*policy"):
        generator.state_from(persisted)


def test_face_event_rejects_uncoupled_target_values() -> None:
    """Merely naming both gaze actuators must not permit crossed-eye targets."""

    generator = _generator()
    with pytest.raises(ValidationError, match="coupled"):
        FaceEvent(
            schema_version="face-event/v1",
            event_id="uncoupled",
            model_id=generator.config.model_id,
            model_sha256=generator.model_sha256,
            kind=FaceEventKind.GAZE,
            starts_monotonic_ns=0,
            onset_s=0.4,
            hold_s=0.8,
            release_s=0.4,
            amplitude=0.1,
            actuator_names=("right_eye_horizontal", "left_eye_horizontal"),
            peak_targets=(
                ActuatorTarget(
                    actuator_name="right_eye_horizontal", normalized_position=0.1
                ),
                ActuatorTarget(
                    actuator_name="left_eye_horizontal", normalized_position=-0.1
                ),
            ),
        )
