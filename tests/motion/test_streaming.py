"""Behavioral tests for stateful overlapping-horizon motion generation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import MotionProposal, TargetUpdate, TargetUpdateHorizon
from alice.motion.anchors import (
    AnchorPlanner,
    ProceduralMotionConfig,
    load_procedural_motion_config,
)
from alice.motion.intent_filter import FilteredIntent, SupportStatus
from alice.motion.procedural import ProceduralMotionGenerator
from alice.motion.state import (
    ActuatorVelocity,
    EventHistoryRecord,
    GeneratorState,
    dump_state,
    load_state,
)
from alice.motion.streaming import (
    CandidatePlan,
    ProceduralCandidateGenerator,
    StreamingMotionGenerator,
)

ROOT = Path(__file__).parents[2]
CONFIG_PATH = ROOT / "config" / "models" / "procedural-motion-v1.yaml"
SHA256_ZERO = "0" * 64


def _intent(*, accepted_ns: int = 0) -> FilteredIntent:
    return FilteredIntent(
        schema_version="filtered-intent/v1",
        affect_schema_id="affect-vector/v1",
        vector=(0.0, 0.0, 0.0),
        intensity=0.7,
        source_id="streaming-test",
        accepted_monotonic_ns=accepted_ns,
        support_status=SupportStatus.SUPPORTED,
        support_distance=0.0,
        reason="deterministic streaming fixture",
    )


def _runtime() -> tuple[StreamingMotionGenerator, ProceduralMotionConfig]:
    config = load_procedural_motion_config(CONFIG_PATH)
    planner = AnchorPlanner(config=config)
    generator = ProceduralMotionGenerator(config=config, anchor_planner=planner)
    return (
        StreamingMotionGenerator(
            generator=ProceduralCandidateGenerator(generator=generator),
            horizon_s=1.0,
            prefix_duration_s=0.4,
        ),
        config,
    )


def _state(config: ProceduralMotionConfig, *, now_ns: int = 0) -> GeneratorState:
    neutral = config.anchor("neutral")
    target = TargetUpdate(offset_s=0.0, targets=neutral.targets)
    rng = np.random.default_rng(47)
    return GeneratorState(
        schema_version="generator-state/v1",
        last_accepted_target=target,
        last_reported_pose=target,
        estimated_velocity=tuple(
            ActuatorVelocity(actuator_name=item.actuator_name, velocity_per_s=0.0)
            for item in target.targets
        ),
        filtered_intent=_intent(accepted_ns=now_ns),
        latent_vector=(0.1, -0.2, 0.3),
        numpy_rng_state=rng.bit_generator.state,
        torch_rng_state=(9, 8, 7, 6),
        event_history=(
            EventHistoryRecord(
                event_type="test/blink-v1",
                started_monotonic_ns=now_ns,
                ended_monotonic_ns=now_ns + 10,
                payload={"amplitude": 0.25, "side": "both"},
            ),
        ),
        model_id=config.model_id,
        model_sha256=hashlib.sha256(
            config.model_dump_json().encode("utf-8")
        ).hexdigest(),
        calibration_sha256=config.calibration_sha256,
        controller_settings_sha256=config.controller_settings_sha256,
        monotonic_ns=now_ns,
    )


def test_replanning_starts_at_last_accepted_state() -> None:
    """Planning from the original bootstrap pose would jump at prefix boundaries."""

    runtime, config = _runtime()
    first, state1 = runtime.replan(_intent(), _state(config), 0)
    second, _ = runtime.replan(
        _intent(accepted_ns=first.ends_at_ns),
        state1,
        first.ends_at_ns,
    )

    assert second.updates[0].targets == first.updates[-1].targets


def test_state_serialization_preserves_rng_replay() -> None:
    """Dropping RNG internals during JSON persistence would change the next horizon."""

    runtime, config = _runtime()
    state = _state(config)

    direct = runtime.replan(_intent(), state, 0)
    restored = runtime.replan(_intent(), load_state(dump_state(state)), 0)

    assert restored == direct


def test_state_round_trip_preserves_continuation_envelope() -> None:
    """Omitting continuation fields would make process restart behavior ambiguous."""

    _, config = _runtime()
    state = _state(config)

    assert load_state(dump_state(state)) == state


def test_state_accepts_array_backed_numpy_rng_for_portable_replay() -> None:
    """Persisting only scalar RNG states would silently exclude NumPy MT19937."""

    runtime, config = _runtime()
    payload = _state(config).model_dump()
    payload["numpy_rng_state"] = np.random.MT19937(47).state

    state = GeneratorState.model_validate(payload)
    restored = load_state(dump_state(state))

    assert restored == state
    assert runtime.replan(_intent(), restored, 0) == runtime.replan(_intent(), state, 0)


def test_replan_emits_only_the_configured_prefix() -> None:
    """Returning the full candidate would defeat overlapping-horizon replanning."""

    runtime, config = _runtime()

    prefix, next_state = runtime.replan(_intent(), _state(config), 0)

    assert [update.offset_s for update in prefix.updates] == pytest.approx(
        [0.0, 0.2, 0.4]
    )
    assert prefix.starts_at_ns == 0
    assert prefix.ends_at_ns == 400_000_000
    assert next_state.monotonic_ns == prefix.ends_at_ns
    assert next_state.last_accepted_target.targets == prefix.updates[-1].targets
    assert next_state.last_accepted_target.offset_s == 0.0


def test_replan_rejects_state_identity_change() -> None:
    """Continuing state from different model weights would invalidate replay."""

    runtime, config = _runtime()
    changed = _state(config).model_copy(update={"model_sha256": SHA256_ZERO})

    with pytest.raises(ValueError, match="model identity"):
        runtime.replan(_intent(), changed, 0)


def test_replan_rejects_time_before_persisted_boundary() -> None:
    """Moving absolute time backward would overlap an already accepted prefix."""

    runtime, config = _runtime()
    state = _state(config, now_ns=10)

    with pytest.raises(ValueError, match="now_ns must not precede"):
        runtime.replan(_intent(accepted_ns=10), state, 9)


def test_replan_rejects_intent_from_the_future() -> None:
    """Persisting a future-filtered intent would corrupt the absolute timeline."""

    runtime, config = _runtime()

    with pytest.raises(ValueError, match="filtered intent postdates replan time"):
        runtime.replan(_intent(accepted_ns=1), _state(config), 0)


def test_replan_rejects_filtered_intent_timestamp_regression() -> None:
    """An older filter result must not replace newer persisted affect state."""

    runtime, config = _runtime()

    with pytest.raises(ValueError, match="filtered intent timestamp regressed"):
        runtime.replan(_intent(accepted_ns=9), _state(config, now_ns=10), 10)


@pytest.mark.parametrize(
    "intent",
    [
        pytest.param(
            _intent().model_copy(update={"affect_schema_id": "other-schema/v1"}),
            id="schema-identity",
        ),
        pytest.param(
            _intent().model_copy(update={"vector": (0.0, 0.0)}),
            id="dimension-count",
        ),
    ],
)
def test_replan_rejects_incompatible_filtered_intent(intent: FilteredIntent) -> None:
    """Incompatible affect state must be rejected before replacing persisted state."""

    runtime, config = _runtime()

    with pytest.raises(ValueError, match="filtered intent.*incompatible"):
        runtime.replan(intent, _state(config), 0)


class _StatefulCandidate:
    """Pure candidate that advances every dynamic continuation-state family."""

    def plan(
        self,
        intent: FilteredIntent,
        state: GeneratorState,
        horizon_s: float,
        prefix_duration_s: float,
        *,
        generated_monotonic_ns: int,
    ) -> CandidatePlan:
        bit_generator = np.random.PCG64()
        bit_generator.state = state.numpy_rng_state
        rng = np.random.Generator(bit_generator)
        seed = int(rng.integers(0, 1_000_000))
        delta = float(rng.uniform(0.001, 0.01))

        start_positions = {
            target.actuator_name: target.normalized_position
            for target in state.last_accepted_target.targets
        }
        first_name = state.last_accepted_target.targets[0].actuator_name
        boundary_positions = dict(start_positions)
        boundary_positions[first_name] += delta

        def update(offset_s: float, positions: dict[str, float]) -> TargetUpdate:
            return TargetUpdate(
                offset_s=offset_s,
                targets=tuple(
                    ActuatorTarget(
                        actuator_name=name,
                        normalized_position=position,
                    )
                    for name, position in positions.items()
                ),
            )

        start = update(0.0, start_positions)
        boundary = update(prefix_duration_s, boundary_positions)
        proposal = MotionProposal(
            schema_version="motion-proposal/v1",
            proposal_id=f"stateful-{generated_monotonic_ns}-{seed}",
            run_id="stateful-test",
            generated_monotonic_ns=generated_monotonic_ns,
            expires_monotonic_ns=(
                generated_monotonic_ns + round(horizon_s * 1_000_000_000) + 1
            ),
            seed=seed,
            model_id=state.model_id,
            model_sha256=state.model_sha256,
            calibration_sha256=state.calibration_sha256,
            controller_settings_sha256=state.controller_settings_sha256,
            support_status=intent.support_status.value,
            horizon=TargetUpdateHorizon(
                schema_version="target-update-horizon/v1",
                updates=(
                    start,
                    boundary,
                    update(horizon_s, boundary_positions),
                ),
            ),
        )
        reported_positions = {
            target.actuator_name: target.normalized_position
            for target in state.last_reported_pose.targets
        }
        reported_positions[first_name] += delta / 2.0
        ends_at_ns = generated_monotonic_ns + round(prefix_duration_s * 1_000_000_000)
        state_values = state.model_dump()
        state_values.update(
            {
                "last_accepted_target": boundary.model_copy(update={"offset_s": 0.0}),
                "last_reported_pose": update(0.0, reported_positions),
                "estimated_velocity": tuple(
                    ActuatorVelocity(
                        actuator_name=item.actuator_name,
                        velocity_per_s=item.velocity_per_s + delta,
                    )
                    for item in state.estimated_velocity
                ),
                "filtered_intent": intent,
                "latent_vector": tuple(value + delta for value in state.latent_vector),
                "numpy_rng_state": rng.bit_generator.state,
                "torch_rng_state": tuple(
                    (value + 1) % 256 for value in state.torch_rng_state
                ),
                "event_history": (
                    *state.event_history,
                    EventHistoryRecord(
                        event_type="test/stateful-step-v1",
                        started_monotonic_ns=generated_monotonic_ns,
                        ended_monotonic_ns=ends_at_ns,
                        payload={"ordinal": len(state.event_history)},
                    ),
                ),
                "monotonic_ns": ends_at_ns,
            }
        )
        return CandidatePlan(
            proposal=proposal,
            boundary_state=GeneratorState.model_validate(state_values),
        )


def test_stateful_candidate_replays_after_prefix_boundary_restore() -> None:
    """Dropping candidate-produced state would diverge after a persisted boundary."""

    _, config = _runtime()
    initial = _state(config)
    runtime = StreamingMotionGenerator(
        generator=_StatefulCandidate(),
        horizon_s=1.0,
        prefix_duration_s=0.4,
    )

    _, boundary = runtime.replan(_intent(), initial, 0)
    restored = load_state(dump_state(boundary))
    direct = runtime.replan(
        _intent(accepted_ns=boundary.monotonic_ns),
        boundary,
        boundary.monotonic_ns,
    )
    replayed = runtime.replan(
        _intent(accepted_ns=restored.monotonic_ns),
        restored,
        restored.monotonic_ns,
    )

    assert boundary.last_reported_pose != initial.last_reported_pose
    assert boundary.estimated_velocity != initial.estimated_velocity
    assert boundary.latent_vector != initial.latent_vector
    assert boundary.numpy_rng_state != initial.numpy_rng_state
    assert boundary.torch_rng_state != initial.torch_rng_state
    assert boundary.event_history != initial.event_history
    assert replayed == direct


class _StaticGenerator:
    """Candidate source used to exercise acceptance boundary failures."""

    def __init__(self, template: MotionProposal) -> None:
        self._proposal = template

    def plan(
        self,
        intent: FilteredIntent,
        state: GeneratorState,
        horizon_s: float,
        prefix_duration_s: float,
        *,
        generated_monotonic_ns: int,
    ) -> CandidatePlan:
        del horizon_s
        accepted = tuple(
            update
            for update in self._proposal.horizon.updates
            if update.offset_s <= prefix_duration_s
        )
        state_values = state.model_dump()
        state_values.update(
            {
                "last_accepted_target": accepted[-1].model_copy(
                    update={"offset_s": 0.0}
                ),
                "filtered_intent": intent,
                "monotonic_ns": generated_monotonic_ns
                + round(prefix_duration_s * 1_000_000_000),
            }
        )
        return CandidatePlan(
            proposal=self._proposal,
            boundary_state=GeneratorState.model_validate(state_values),
        )


class _DiscontinuousGenerator(_StaticGenerator):
    """Candidate source that violates the runtime's continuity boundary."""

    def __init__(self, template: MotionProposal) -> None:
        changed_start = TargetUpdate(
            offset_s=0.0,
            targets=tuple(
                ActuatorTarget(
                    actuator_name=target.actuator_name,
                    normalized_position=(
                        0.1
                        if target.actuator_name
                        == template.horizon.updates[0].targets[0].actuator_name
                        else target.normalized_position
                    ),
                )
                for target in template.horizon.updates[0].targets
            ),
        )
        super().__init__(
            template.model_copy(
                update={
                    "horizon": TargetUpdateHorizon(
                        schema_version="target-update-horizon/v1",
                        updates=(changed_start, *template.horizon.updates[1:]),
                    )
                }
            )
        )


def test_replan_rejects_discontinuous_candidate_start() -> None:
    """Accepting a candidate whose first target jumps would break continuity."""

    runtime, config = _runtime()
    state = _state(config)
    generator = ProceduralMotionGenerator(
        config=config,
        anchor_planner=AnchorPlanner(config=config),
    )
    template = generator.step(
        _intent(),
        state.last_accepted_target,
        seed=1,
        horizon_s=1.0,
        generated_monotonic_ns=0,
    )
    discontinuous = StreamingMotionGenerator(
        generator=_DiscontinuousGenerator(template),
        horizon_s=1.0,
        prefix_duration_s=0.4,
    )

    with pytest.raises(ValueError, match="discontinuous candidate start"):
        discontinuous.replan(_intent(), state, 0)


def test_replan_rejects_candidate_that_does_not_cover_the_prefix() -> None:
    """Extending a short candidate by implication would accept unproposed motion."""

    runtime, config = _runtime()
    state = _state(config)
    generator = ProceduralMotionGenerator(
        config=config,
        anchor_planner=AnchorPlanner(config=config),
    )
    proposal = generator.step(
        _intent(),
        state.last_accepted_target,
        seed=1,
        horizon_s=1.0,
        generated_monotonic_ns=0,
    )
    short_proposal = proposal.model_copy(
        update={
            "horizon": TargetUpdateHorizon(
                schema_version="target-update-horizon/v1",
                updates=proposal.horizon.updates[:2],
            )
        }
    )
    short_runtime = StreamingMotionGenerator(
        generator=_StaticGenerator(short_proposal),
        horizon_s=1.0,
        prefix_duration_s=0.4,
    )

    with pytest.raises(ValueError, match="does not cover accepted prefix"):
        short_runtime.replan(_intent(), state, 0)
