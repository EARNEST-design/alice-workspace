"""Behavioral tests for reviewed expression-anchor planning."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.motion.anchors import (
    AffectAnchorMapping,
    AnchorPlanner,
    load_procedural_motion_config,
)
from alice.motion.intent_filter import FilteredIntent, SupportStatus

ROOT = Path(__file__).parents[2]
CONFIG_PATH = ROOT / "config" / "models" / "procedural-motion-v1.yaml"


def _intent(
    *,
    vector: tuple[float, ...] = (0.0, 0.0, 0.0),
    intensity: float = 1.0,
    support_status: SupportStatus = SupportStatus.SUPPORTED,
) -> FilteredIntent:
    return FilteredIntent(
        schema_version="filtered-intent/v1",
        affect_schema_id="affect-vector/v1",
        vector=vector,
        intensity=intensity,
        source_id="anchor-test",
        accepted_monotonic_ns=2_000_000_000,
        support_status=support_status,
        support_distance=0.0 if support_status is not SupportStatus.FALLBACK else None,
        reason="deterministic test fixture",
    )


def _state(planner: AnchorPlanner, **overrides: float) -> TargetUpdate:
    neutral = {
        target.actuator_name: target.normalized_position
        for target in planner.config.anchor("neutral").targets
    }
    neutral.update(overrides)
    return TargetUpdate(
        offset_s=0.0,
        targets=tuple(
            ActuatorTarget(actuator_name=name, normalized_position=value)
            for name, value in neutral.items()
        ),
    )


def _positions(update: TargetUpdate) -> dict[str, float]:
    return {
        target.actuator_name: target.normalized_position
        for target in update.targets
    }


def test_checked_in_anchors_preserve_only_reviewed_semantic_poses() -> None:
    """Invented affect coordinates or channel-number targets could feign evidence."""

    config = load_procedural_motion_config(CONFIG_PATH)
    anchors = {
        anchor.name: _positions(
            TargetUpdate(offset_s=0.0, targets=anchor.targets)
        )
        for anchor in config.anchors
    }

    assert config.affect_anchor_mappings == ()
    assert anchors["smile_open"] == {
        "mouth_open": 1.0,
        "left_mouth_corner": 1.0,
        "right_mouth_corner": -1.0,
    }
    assert anchors["frown_closed"] == {
        "mouth_open": -1.0,
        "left_mouth_corner": -1.0,
        "right_mouth_corner": 1.0,
    }
    assert all(target == 0.0 for target in anchors["neutral"].values())
    assert set(anchors["neutral"]) == set(config.semantic_actuator_names)


def test_nested_drift_configuration_cannot_change_after_identity_hashing() -> None:
    """In-place amplitude mutation would invalidate deterministic model identity."""

    config = load_procedural_motion_config(CONFIG_PATH)

    with pytest.raises(TypeError):
        cast(Any, config.drift.amplitudes)[0] = object()


def test_neutral_plan_starts_at_accepted_state_and_uses_effective_cadence() -> None:
    """Skipping offset zero or inventing intermediate rates would break continuity."""

    planner = AnchorPlanner(config=load_procedural_motion_config(CONFIG_PATH))
    state = _state(planner, head_tilt=0.4, mouth_open=-0.2)

    horizon = planner.plan_neutral(state, horizon_s=1.0)

    assert horizon.updates[0].targets == state.targets
    assert [update.offset_s for update in horizon.updates] == pytest.approx(
        [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    )
    assert set(_positions(horizon.updates[-1]).values()) == {0.0}


def test_fallback_intent_returns_neutral_plan() -> None:
    """Using an unsupported coordinate would invent an affect-to-pose mapping."""

    planner = AnchorPlanner(config=load_procedural_motion_config(CONFIG_PATH))
    state = _state(planner, mouth_open=0.3)

    observed = planner.plan(
        _intent(vector=(0.8, 0.8, 0.8), support_status=SupportStatus.FALLBACK),
        state,
        horizon_s=1.0,
    )

    assert observed == planner.plan_neutral(state, horizon_s=1.0)


def test_evidenced_mapping_interpolates_between_reviewed_anchor_targets() -> None:
    """Choosing a label or extrapolated target would leave the reviewed envelope."""

    base = load_procedural_motion_config(CONFIG_PATH)
    config = base.model_copy(
        update={
            "affect_anchor_mappings": (
                AffectAnchorMapping(
                    anchor_name="frown_closed",
                    coordinate=(-1.0, 0.0, 0.0),
                    provenance="unit-test reviewed mapping",
                ),
                AffectAnchorMapping(
                    anchor_name="smile_open",
                    coordinate=(1.0, 0.0, 0.0),
                    provenance="unit-test reviewed mapping",
                ),
            )
        }
    )
    planner = AnchorPlanner(config=config)

    horizon = planner.plan(
        _intent(vector=(0.5, 0.0, 0.0)),
        _state(planner),
        horizon_s=1.0,
    )

    positions = _positions(horizon.updates[-1])
    assert positions["mouth_open"] == pytest.approx(0.5)
    assert positions["left_mouth_corner"] == pytest.approx(0.5)
    assert positions["right_mouth_corner"] == pytest.approx(-0.5)
    assert all(-1.0 <= value <= 1.0 for value in positions.values())


@pytest.mark.parametrize("horizon_s", [0.0, -0.1, float("inf"), float("nan")])
def test_anchor_planner_rejects_invalid_horizon(horizon_s: float) -> None:
    """An empty or non-finite planning window cannot define proposal validity."""

    planner = AnchorPlanner(config=load_procedural_motion_config(CONFIG_PATH))

    with pytest.raises(ValueError, match="horizon_s"):
        planner.plan_neutral(_state(planner), horizon_s)
