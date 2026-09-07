"""End-to-end deterministic replay for the affect-motion foundation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from alice.contracts.affect import AffectIntent, AffectVectorSchema
from alice.contracts.motion import MotionProposal, TargetUpdate
from alice.motion.anchors import AnchorPlanner, load_procedural_motion_config
from alice.motion.controller_response import (
    ControllerResponse,
    ControllerState,
    load_controller_response_config,
)
from alice.motion.intent_filter import (
    FilteredIntent,
    IntentFilter,
    IntentFilterConfig,
    SupportStatus,
)
from alice.motion.procedural import ProceduralMotionGenerator

ROOT = Path(__file__).parents[2]
AFFECT_SCHEMA_PATH = ROOT / "config" / "affect" / "affect-vector-v1.yaml"
FILTER_CONFIG_PATH = ROOT / "config" / "affect" / "intent-filter-v1.yaml"
PROCEDURAL_CONFIG_PATH = ROOT / "config" / "models" / "procedural-motion-v1.yaml"
RESPONSE_CONFIG_PATH = ROOT / "config" / "models" / "maestro-response-v1.yaml"
GENERATED_NS = 2_100_000_000


def _supported_intent() -> FilteredIntent:
    schema = AffectVectorSchema.model_validate(
        yaml.safe_load(AFFECT_SCHEMA_PATH.read_text(encoding="utf-8"))
    )
    filter_document = yaml.safe_load(FILTER_CONFIG_PATH.read_text(encoding="utf-8"))
    filter_document.update(
        {
            "retained_training_coordinates": [[0.0, 0.0, 0.0]],
            "support_set_id": "synthetic-foundation-replay-v1",
            "support_provenance": "hand-authored deterministic test fixture",
        }
    )
    intent_filter = IntentFilter(
        schema=schema,
        config=IntentFilterConfig.model_validate(filter_document),
    )
    previous = FilteredIntent(
        schema_version="filtered-intent/v1",
        affect_schema_id=schema.schema_id,
        vector=(0.0, 0.0, 0.0),
        intensity=0.0,
        source_id="replay-bootstrap",
        accepted_monotonic_ns=1_000_000_000,
        support_status=SupportStatus.SUPPORTED,
        support_distance=0.0,
        reason="synthetic replay bootstrap",
    )
    requested = AffectIntent(
        schema_version="affect-intent/v1",
        affect_schema_id=schema.schema_id,
        vector=(0.0, 0.0, 0.0),
        intensity=0.7,
        source_id="replay-intent",
        captured_at=datetime(2026, 9, 7, tzinfo=UTC),
        received_monotonic_ns=2_000_000_000,
        expires_monotonic_ns=3_000_000_000,
    )
    return intent_filter.update(requested, previous, now_ns=2_000_000_000)


def _replay(
    proposal: MotionProposal,
    *,
    initial_update: TargetUpdate,
    response: ControllerResponse,
) -> tuple[ControllerState, ...]:
    states = {
        target.actuator_name: ControllerState(
            schema_version="controller-state/v1",
            actuator_name=target.actuator_name,
            calibration_sha256=response.config.calibration_sha256,
            position=target.normalized_position,
            velocity=0.0,
        )
        for target in initial_update.targets
    }
    previous_offset = proposal.horizon.updates[0].offset_s
    for update in proposal.horizon.updates[1:]:
        elapsed_s = update.offset_s - previous_offset
        for actuator_name, state in tuple(states.items()):
            states[actuator_name] = response.predict(
                state,
                update,
                elapsed_s=elapsed_s,
            )
        previous_offset = update.offset_s
    return tuple(states[name] for name in sorted(states))


def test_multi_seed_intent_to_controller_replay_is_deterministic_and_feasible() -> None:
    """Response-infeasible events or shared RNG state would break full replay."""

    intent = _supported_intent()
    procedural_config = load_procedural_motion_config(PROCEDURAL_CONFIG_PATH)
    planner = AnchorPlanner(config=procedural_config)
    generator = ProceduralMotionGenerator(
        config=procedural_config,
        anchor_planner=planner,
    )
    response = ControllerResponse(
        config=load_controller_response_config(RESPONSE_CONFIG_PATH)
    )
    initial_update = TargetUpdate(
        offset_s=0.0,
        targets=procedural_config.anchor("neutral").targets,
    )
    distinct_horizons = set()

    for seed in range(8):
        first = generator.step(
            intent,
            initial_update,
            seed=seed,
            horizon_s=12.0,
            generated_monotonic_ns=GENERATED_NS,
        )
        second = generator.step(
            intent,
            initial_update,
            seed=seed,
            horizon_s=12.0,
            generated_monotonic_ns=GENERATED_NS,
        )
        decoded_first = MotionProposal.model_validate_json(first.model_dump_json())
        decoded_second = MotionProposal.model_validate_json(second.model_dump_json())

        assert decoded_first == decoded_second
        assert _replay(
            decoded_first,
            initial_update=initial_update,
            response=response,
        ) == _replay(
            decoded_second,
            initial_update=initial_update,
            response=response,
        )
        distinct_horizons.add(decoded_first.horizon.model_dump_json())

    assert intent.support_status is SupportStatus.SUPPORTED
    assert len(distinct_horizons) > 1
