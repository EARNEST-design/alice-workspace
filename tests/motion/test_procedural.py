"""Behavioral tests for the seeded procedural living-motion baseline."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import MotionProposal, TargetUpdate
from alice.motion.anchors import AnchorPlanner, load_procedural_motion_config
from alice.motion.intent_filter import FilteredIntent, SupportStatus
from alice.motion.procedural import ProceduralMotionGenerator

ROOT = Path(__file__).parents[2]
CONFIG_PATH = ROOT / "config" / "models" / "procedural-motion-v1.yaml"
GENERATED_NS = 10_000_000_000


def _intent(status: SupportStatus = SupportStatus.SUPPORTED) -> FilteredIntent:
    return FilteredIntent(
        schema_version="filtered-intent/v1",
        affect_schema_id="affect-vector/v1",
        vector=(0.0, 0.0, 0.0),
        intensity=0.7,
        source_id="procedural-test",
        accepted_monotonic_ns=2_000_000_000,
        support_status=status,
        support_distance=0.0 if status is not SupportStatus.FALLBACK else None,
        reason="deterministic test fixture",
    )


def _state(planner: AnchorPlanner, **overrides: float) -> TargetUpdate:
    positions = {
        target.actuator_name: target.normalized_position
        for target in planner.config.anchor("neutral").targets
    }
    positions.update(overrides)
    return TargetUpdate(
        offset_s=0.0,
        targets=tuple(
            ActuatorTarget(actuator_name=name, normalized_position=value)
            for name, value in positions.items()
        ),
    )


def _generator() -> tuple[AnchorPlanner, ProceduralMotionGenerator]:
    config = load_procedural_motion_config(CONFIG_PATH)
    planner = AnchorPlanner(config=config)
    return planner, ProceduralMotionGenerator(config=config, anchor_planner=planner)


def _position(update: TargetUpdate, actuator_name: str) -> float:
    return next(
        target.normalized_position
        for target in update.targets
        if target.actuator_name == actuator_name
    )


def test_same_seed_replays_identically() -> None:
    """Using ambient or shared RNG state would make replay diverge."""

    planner, generator = _generator()
    state = _state(planner)

    assert generator.step(
        _intent(),
        state,
        41,
        2.0,
        generated_monotonic_ns=GENERATED_NS,
    ) == generator.step(
        _intent(),
        state,
        41,
        2.0,
        generated_monotonic_ns=GENERATED_NS,
    )


def test_generated_json_round_trips_through_canonical_motion_proposal() -> None:
    """Adding a subtype-only wire field would violate the canonical v1 contract."""

    planner, generator = _generator()
    generated = generator.step(
        _intent(),
        _state(planner),
        seed=41,
        horizon_s=2.0,
        generated_monotonic_ns=GENERATED_NS,
    )

    decoded = MotionProposal.model_validate_json(generated.model_dump_json())

    assert type(generated) is MotionProposal
    assert decoded == generated


def test_long_gap_fallback_uses_explicit_proposal_generation_time() -> None:
    """Reusing retained intent time would create a newly generated stale proposal."""

    planner, generator = _generator()
    intent = _intent(SupportStatus.FALLBACK)

    proposal = generator.step(
        intent,
        _state(planner),
        seed=0,
        horizon_s=1.0,
        generated_monotonic_ns=GENERATED_NS,
    )

    assert intent.accepted_monotonic_ns == 2_000_000_000
    assert proposal.generated_monotonic_ns == GENERATED_NS
    assert proposal.is_expired(now_monotonic_ns=GENERATED_NS) is False


def test_generation_time_participates_in_proposal_identity() -> None:
    """Reusing an ID for proposals born at different times would be ambiguous."""

    planner, generator = _generator()
    state = _state(planner)

    first = generator.step(
        _intent(),
        state,
        seed=0,
        horizon_s=1.0,
        generated_monotonic_ns=GENERATED_NS,
    )
    later = generator.step(
        _intent(),
        state,
        seed=0,
        horizon_s=1.0,
        generated_monotonic_ns=GENERATED_NS + 1,
    )

    assert first.horizon == later.horizon
    assert first.proposal_id != later.proposal_id


def test_generated_updates_are_strictly_before_exclusive_proposal_expiry() -> None:
    """An update at the deadline could be scheduled only after proposal rejection."""

    planner, generator = _generator()

    proposal = generator.step(
        _intent(),
        _state(planner),
        seed=5,
        horizon_s=1.0,
        generated_monotonic_ns=GENERATED_NS,
    )

    validity_s = (
        proposal.expires_monotonic_ns - proposal.generated_monotonic_ns
    ) / 1_000_000_000
    assert proposal.horizon.updates[-1].offset_s == 1.0
    assert all(
        update.offset_s < validity_s for update in proposal.horizon.updates
    )


def test_different_seeds_produce_distinct_slow_variation() -> None:
    """Ignoring the seed would collapse the procedural baseline to one trajectory."""

    planner, generator = _generator()
    state = _state(planner)
    first = generator.step(
        _intent(), state, 41, 2.0, generated_monotonic_ns=GENERATED_NS
    )
    second = generator.step(
        _intent(), state, 42, 2.0, generated_monotonic_ns=GENERATED_NS
    )

    assert first.horizon != second.horizon


def test_unsupported_coordinate_returns_exact_anchor_fallback() -> None:
    """Procedural variation on unsupported affect would disguise fallback behavior."""

    planner, generator = _generator()
    state = _state(planner, mouth_open=0.2)

    proposal = generator.step(
        _intent(SupportStatus.FALLBACK),
        state,
        seed=7,
        horizon_s=1.0,
        generated_monotonic_ns=GENERATED_NS,
    )

    assert proposal.support_status == "fallback"
    assert proposal.horizon == planner.plan_neutral(state, 1.0)


def test_checked_in_empty_support_forces_neutral_fallback() -> None:
    """The checked-in absence of empirical support must remain visible downstream."""

    planner, generator = _generator()
    state = _state(planner)

    proposal = generator.step(
        _intent(SupportStatus.FALLBACK),
        state,
        seed=17,
        horizon_s=2.0,
        generated_monotonic_ns=GENERATED_NS,
    )

    assert proposal.support_status == "fallback"
    assert proposal.horizon == planner.plan_neutral(state, 2.0)


def test_procedural_updates_start_at_state_and_follow_effective_cadence() -> None:
    """A random first offset would create a horizon boundary jump."""

    planner, generator = _generator()
    state = _state(planner, head_tilt=0.1)

    proposal = generator.step(
        _intent(),
        state,
        seed=5,
        horizon_s=1.0,
        generated_monotonic_ns=GENERATED_NS,
    )

    assert proposal.horizon.updates[0].targets == state.targets
    assert [update.offset_s for update in proposal.horizon.updates] == pytest.approx(
        [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    )


def test_seeded_drift_is_band_limited_instead_of_per_update_jitter() -> None:
    """Independent random offsets at each update would exceed the configured slope."""

    planner, generator = _generator()
    proposal = generator.step(
        _intent(),
        _state(planner),
        seed=11,
        horizon_s=6.0,
        generated_monotonic_ns=GENERATED_NS,
    )
    values = [
        _position(update, "head_tilt") for update in proposal.horizon.updates
    ]
    period_s = 1.0 / generator.config.effective_cadence_hz
    slope_bound = (
        generator.config.drift.amplitude("head_tilt")
        * 2.0
        * math.pi
        * generator.config.drift.max_frequency_hz
    )

    assert max(abs(right - left) for left, right in zip(values, values[1:])) <= (
        slope_bound * period_s + 1e-12
    )


def test_blink_and_gaze_timers_are_coupled_and_respect_refractory_windows() -> None:
    """Independent eye events or repeated closures would create visible twitching."""

    planner, generator = _generator()
    proposal = generator.step(
        _intent(),
        _state(planner),
        seed=23,
        horizon_s=20.0,
        generated_monotonic_ns=GENERATED_NS,
    )
    updates = proposal.horizon.updates

    for update in updates:
        assert _position(update, "lower_eyelids") == pytest.approx(
            _position(update, "upper_eyelids")
        )
        assert _position(update, "left_eye_horizontal") == pytest.approx(
            _position(update, "right_eye_horizontal")
        )

    blink_times = [
        update.offset_s
        for update in updates
        if _position(update, "lower_eyelids")
        <= -0.5 * generator.config.blink.amplitude
    ]
    blink_onsets = [
        instant
        for index, instant in enumerate(blink_times)
        if index == 0
        or instant - blink_times[index - 1]
        > 1.5 / generator.config.effective_cadence_hz
    ]

    assert len(blink_onsets) >= 2
    assert min(
        right - left for left, right in zip(blink_onsets, blink_onsets[1:])
    ) >= generator.config.blink.refractory_s

    gaze_times = [
        update.offset_s
        for update in updates
        if abs(_position(update, "left_eye_horizontal")) > 1e-12
    ]
    gaze_windows: list[tuple[float, float]] = []
    for instant in gaze_times:
        if not gaze_windows or instant - gaze_windows[-1][1] > (
            1.5 / generator.config.effective_cadence_hz
        ):
            gaze_windows.append((instant, instant))
        else:
            gaze_windows[-1] = (gaze_windows[-1][0], instant)

    assert len(gaze_windows) >= 2
    assert min(
        right_start - left_end
        for (_, left_end), (right_start, _) in zip(
            gaze_windows,
            gaze_windows[1:],
        )
    ) >= generator.config.gaze.refractory_s
