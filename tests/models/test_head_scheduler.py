"""Behavioral tests for configured, affect-conditioned head scheduling."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.models.head_scheduler import (
    HeadGestureConfig,
    HeadGestureScheduler,
    load_head_gesture_config,
)
from alice.motion.controller_response import load_controller_response_config
from alice.motion.head_primitives import HeadGesture, HeadGestureKind
from alice.motion.intent_filter import FilteredIntent, SupportStatus
from alice.motion.state import EventHistoryRecord

ROOT = Path(__file__).parents[2]
CONFIG_PATH = ROOT / "config" / "models" / "head-gestures-v1.yaml"
RESPONSE_PATH = ROOT / "config" / "models" / "maestro-response-v1.yaml"


def _intent(
    *,
    vector: tuple[float, float, float] = (0.0, 0.0, 0.0),
    intensity: float = 0.5,
    accepted_ns: int = 10_000_000_000,
) -> FilteredIntent:
    return FilteredIntent(
        schema_version="filtered-intent/v1",
        affect_schema_id="affect-vector/v1",
        vector=vector,
        intensity=intensity,
        source_id="head-scheduler-test",
        accepted_monotonic_ns=accepted_ns,
        support_status=SupportStatus.SUPPORTED,
        support_distance=0.0,
        reason="deterministic head scheduler fixture",
    )


def _scheduler(config: HeadGestureConfig | None = None) -> HeadGestureScheduler:
    return HeadGestureScheduler(
        config=config or load_head_gesture_config(CONFIG_PATH),
        controller_config=load_controller_response_config(RESPONSE_PATH),
    )


def _certain_scheduler() -> HeadGestureScheduler:
    config = load_head_gesture_config(CONFIG_PATH)
    gestures = tuple(
        policy.model_copy(
            update={
                "base_hazard_hz": 1_000.0,
                "min_hazard_hz": 1_000.0,
                "max_hazard_hz": 1_000.0,
            }
        )
        for policy in config.gestures
    )
    return _scheduler(config.model_copy(update={"gestures": gestures}))


def _sample_sequence(seed: int) -> tuple[HeadGesture | None, ...]:
    scheduler = _scheduler()
    rng = np.random.default_rng(seed)
    history: tuple[EventHistoryRecord, ...] = ()
    observed: list[HeadGesture | None] = []
    for index in range(200):
        gesture = scheduler.sample(
            _intent(accepted_ns=index * 500_000_000),
            history,
            rng,
        )
        observed.append(gesture)
        if gesture is not None:
            history = scheduler.record(history, gesture)
    return tuple(observed)


def test_scheduler_is_seeded_but_not_static() -> None:
    """Ambient RNG use or ignored seeds would break replay or variation."""

    assert _sample_sequence(17) == _sample_sequence(17)
    assert _sample_sequence(17) != _sample_sequence(18)


def test_scheduler_uses_generation_time_and_persists_empty_decisions() -> None:
    scheduler = _scheduler()
    intent = _intent(accepted_ns=1)
    history = scheduler.record_decision((), generated_monotonic_ns=9_000_000_000)

    assert (
        scheduler.decision_due(history, generated_monotonic_ns=9_100_000_000) is False
    )
    assert scheduler.decision_due(history, generated_monotonic_ns=9_500_000_000) is True
    gesture = _certain_scheduler().sample(
        intent,
        (),
        np.random.default_rng(1),
        generated_monotonic_ns=9_000_000_000,
    )
    assert gesture is not None
    assert gesture.starts_monotonic_ns == 9_000_000_000


def test_scheduler_respects_global_and_same_gesture_refractory() -> None:
    """Ignoring accepted history could repeat head motion immediately."""

    scheduler = _scheduler()
    first = next(item for item in _sample_sequence(4) if item is not None)
    history = (first.as_history_record(),)

    during_global_refractory = scheduler.sample(
        _intent(accepted_ns=first.ends_monotonic_ns + 1),
        history,
        np.random.default_rng(2),
    )
    elapsed_same_s = scheduler.config.policy(first.kind).refractory_s - 0.01
    during_same_refractory = scheduler.hazard_rate_hz(
        first.kind,
        _intent(),
        elapsed_since_end_s=elapsed_same_s,
    )

    assert during_global_refractory is None
    assert during_same_refractory == 0.0


def test_scheduler_hazard_depends_on_continuous_affect_and_recovery() -> None:
    """A constant timer would ignore both requested affect and event history."""

    scheduler = _scheduler()
    kind = HeadGestureKind.NOD
    neutral = scheduler.hazard_rate_hz(
        kind,
        _intent(vector=(0.0, 0.0, 0.0), intensity=0.0),
        elapsed_since_end_s=float("inf"),
    )
    aroused = scheduler.hazard_rate_hz(
        kind,
        _intent(vector=(0.0, 1.0, 0.0), intensity=1.0),
        elapsed_since_end_s=float("inf"),
    )
    recovering = scheduler.hazard_rate_hz(
        kind,
        _intent(vector=(0.0, 1.0, 0.0), intensity=1.0),
        elapsed_since_end_s=scheduler.config.policy(kind).refractory_s + 0.01,
    )

    assert aroused > neutral > 0.0
    assert 0.0 < recovering < aroused


def test_sampled_parameters_stay_within_configured_bounds() -> None:
    """An unconstrained scheduler could emit infeasible primitive parameters."""

    scheduler = _scheduler()
    gestures = tuple(item for item in _sample_sequence(29) if item is not None)

    assert gestures
    for gesture in gestures:
        policy = scheduler.config.policy(gesture.kind)
        assert (
            policy.amplitude.minimum
            <= abs(gesture.amplitude)
            <= policy.amplitude.maximum
        )
        assert (
            policy.duration_s.minimum <= gesture.duration_s <= policy.duration_s.maximum
        )
        assert policy.cycles.minimum <= gesture.cycles <= policy.cycles.maximum
        assert policy.asymmetry.minimum <= gesture.asymmetry <= policy.asymmetry.maximum
        assert policy.hold_s.minimum <= gesture.hold_s <= policy.hold_s.maximum
        assert (
            policy.recovery_s.minimum <= gesture.recovery_s <= policy.recovery_s.maximum
        )


def test_generic_history_preserves_face_events_when_recording_head_gesture() -> None:
    """Head persistence must not erase independently scheduled face events."""

    scheduler = _scheduler()
    face_record = EventHistoryRecord(
        event_type="face-event/v1",
        started_monotonic_ns=1,
        ended_monotonic_ns=2,
        payload={"kind": "blink"},
    )
    gesture = next(item for item in _sample_sequence(5) if item is not None)

    history = scheduler.record((face_record,), gesture)

    assert history[0] == face_record
    assert HeadGesture.from_history_record(history[1]) == gesture


def test_non_head_history_does_not_change_head_decision() -> None:
    """Concurrent face events are allowed and must not become head conflicts."""

    scheduler = _scheduler()
    face_record = EventHistoryRecord(
        event_type="face-event/v1",
        started_monotonic_ns=9_900_000_000,
        ended_monotonic_ns=11_000_000_000,
        payload={"kind": "gaze"},
    )

    empty = scheduler.sample(_intent(), (), np.random.default_rng(41))
    with_face = scheduler.sample(
        _intent(),
        (face_record,),
        np.random.default_rng(41),
    )

    assert with_face == empty


def test_mixed_past_and_future_head_history_defers_new_gesture() -> None:
    """A past end must not hide an already accepted future head gesture."""

    scheduler = _certain_scheduler()
    past = scheduler.sample(_intent(accepted_ns=0), (), np.random.default_rng(1))
    assert past is not None
    future_ns = past.ends_monotonic_ns + 6_000_000_000
    future = scheduler.sample(
        _intent(accepted_ns=future_ns),
        (past.as_history_record(),),
        np.random.default_rng(2),
    )
    assert future is not None
    decision_ns = future.starts_monotonic_ns - 1_000_000_000

    proposed = scheduler.sample(
        _intent(accepted_ns=decision_ns),
        (past.as_history_record(), future.as_history_record()),
        np.random.default_rng(3),
    )

    assert proposed is None


def test_checked_in_config_is_an_explicit_unlearned_prior() -> None:
    """Absent labeled episodes must not be presented as learned evidence."""

    config = load_head_gesture_config(CONFIG_PATH)

    assert config.scheduler_kind == "configured-prior"
    assert config.labeled_gesture_episodes == 0
    assert "not fitted" in config.provenance.lower()


def test_learned_claim_without_labeled_episodes_is_rejected() -> None:
    """Changing only a label must not manufacture learned-scheduler evidence."""

    document = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    document["scheduler_kind"] = "learned"

    with pytest.raises(ValidationError, match="scheduler_kind"):
        HeadGestureConfig.model_validate(document)


def test_checked_in_parameter_extremes_obey_quintic_derivative_limits() -> None:
    """Legal extrema must independently satisfy emitted quintic kinematics."""

    scheduler = _scheduler()
    response = load_controller_response_config(RESPONSE_PATH)

    for policy in scheduler.config.gestures:
        parameters = response.actuator(policy.actuator_name)
        if policy.kind in {HeadGestureKind.NOD, HeadGestureKind.SHAKE}:
            transition_s = policy.duration_s.minimum / (2 * policy.cycles.maximum)
            distance = 2.0 * policy.amplitude.maximum
        else:
            transition_s = policy.duration_s.minimum
            distance = policy.amplitude.maximum
        peak_velocity = (15.0 / 8.0) * distance / transition_s
        peak_acceleration = (10.0 * math.sqrt(3.0) / 3.0) * distance / transition_s**2
        recovery_velocity = (
            (15.0 / 8.0) * policy.amplitude.maximum / policy.recovery_s.minimum
        )
        recovery_acceleration = (
            (10.0 * math.sqrt(3.0) / 3.0)
            * policy.amplitude.maximum
            / policy.recovery_s.minimum**2
        )

        assert peak_velocity <= parameters.max_velocity_per_s
        assert peak_acceleration <= parameters.max_acceleration_per_s2
        assert recovery_velocity <= parameters.max_velocity_per_s
        assert recovery_acceleration <= parameters.max_acceleration_per_s2


@pytest.mark.parametrize("position", [0.0, 0.01])
def test_seed25_shake_recovers_to_accepted_pose_and_restores_history(
    position: float,
) -> None:
    """Configured neutral recovery must not erase drift on the active axis."""
    scheduler = _scheduler()
    pose = TargetUpdate(
        offset_s=0.0,
        targets=tuple(
            ActuatorTarget(actuator_name=name, normalized_position=position)
            for name in scheduler.config.semantics.actuator_names
        ),
    )
    gesture = scheduler.sample(
        _intent(),
        (),
        np.random.default_rng(25),
        accepted_pose=pose,
    )
    assert gesture is not None
    assert gesture.kind is HeadGestureKind.SHAKE
    assert gesture.initial_targets == pose.targets
    assert gesture.recovery_targets == pose.targets
    horizon = scheduler.primitives.render(gesture, pose)
    assert horizon.updates[-1].targets == pose.targets
    assert any(update.targets != pose.targets for update in horizon.updates)
    history = scheduler.record((), gesture)
    restored = HeadGesture.from_history_record(history[0])
    assert scheduler.record(history, restored) == history
    assert scheduler.primitives.render(restored, pose) == horizon
    assert (
        scheduler.sample(
            _intent(), history, np.random.default_rng(25), accepted_pose=pose
        )
        is None
    )


def test_head_history_rejects_recovery_different_from_captured_pose() -> None:
    """A changed recovery payload must not silently replay another trajectory."""
    scheduler = _scheduler()
    gesture = scheduler.sample(_intent(), (), np.random.default_rng(25))
    assert gesture is not None
    wrong = tuple(
        t.model_copy(update={"normalized_position": 0.01})
        for t in gesture.recovery_targets
    )
    altered = gesture.model_copy(update={"initial_targets": wrong})
    with pytest.raises(ValueError, match="recovery"):
        scheduler.record((), altered)
