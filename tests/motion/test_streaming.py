"""Behavioral tests for stateful overlapping-horizon motion generation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import torch

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import MotionProposal, TargetUpdate, TargetUpdateHorizon
from alice.models.head_scheduler import (
    HeadGestureScheduler,
    load_head_gesture_config,
)
from alice.models.residual_state_space import (
    ResidualStateSpace,
    load_residual_state_space_config,
)
from alice.motion.anchors import (
    AnchorPlanner,
    ProceduralMotionConfig,
    load_procedural_motion_config,
)
from alice.motion.controller_response import (
    ControllerResponse,
    ControllerState,
    load_controller_response_config,
)
from alice.motion.face_events import (
    FaceEvent,
    FaceEventGenerator,
    FaceEventKind,
    FaceEventState,
    load_face_event_config,
)
from alice.motion.head_primitives import HeadGesture, HeadGestureKind
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
    ProductionCandidateComposer,
    StreamingMotionGenerator,
)

ROOT = Path(__file__).parents[2]
CONFIG_PATH = ROOT / "config" / "models" / "procedural-motion-v1.yaml"
CONTROLLER_CONFIG_PATH = ROOT / "config" / "models" / "maestro-response-v1.yaml"
FACE_CONFIG_PATH = ROOT / "config" / "models" / "face-events-v1.yaml"
HEAD_CONFIG_PATH = ROOT / "config" / "models" / "head-gestures-v1.yaml"
RESIDUAL_CONFIG_PATH = ROOT / "config" / "models" / "residual-state-space-v1.yaml"
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


def test_moving_blink_falls_back_atomically_to_controller_feasible_anchor() -> None:
    """An event target inside stopping distance must not leak attempted state."""

    moving_position = -0.4664151512
    moving_velocity = -0.3869662820
    requested_target = -0.4718372893
    generated_ns = 1_000_000_000
    controller_config = load_controller_response_config(CONTROLLER_CONFIG_PATH)
    response = ControllerResponse(config=controller_config)
    anchor_config = load_procedural_motion_config(CONFIG_PATH).model_copy(
        update={"anchor_transition_s": 0.1}
    )
    residual = ResidualStateSpace(
        load_residual_state_space_config(RESIDUAL_CONFIG_PATH)
    )
    with torch.no_grad():
        for parameter in residual.parameters():
            parameter.zero_()
    face = FaceEventGenerator(
        config=load_face_event_config(FACE_CONFIG_PATH),
        controller_config=controller_config,
    )
    head = HeadGestureScheduler(
        config=load_head_gesture_config(HEAD_CONFIG_PATH),
        controller_config=controller_config,
    )
    composer = ProductionCandidateComposer(
        anchor_planner=AnchorPlanner(config=anchor_config),
        residual_model=residual,
        face_events=face,
        head_scheduler=head,
        controller_response=response,
    )
    neutral = anchor_config.anchor("neutral")
    positions = {
        target.actuator_name: target.normalized_position for target in neutral.targets
    }
    positions["upper_eyelids"] = moving_position
    target = ProductionCandidateComposer._update(0.0, positions)
    rng = np.random.default_rng(7)
    state = GeneratorState(
        schema_version="generator-state/v1",
        last_accepted_target=target,
        last_reported_pose=target,
        estimated_velocity=tuple(
            ActuatorVelocity(
                actuator_name=item.actuator_name,
                velocity_per_s=(
                    moving_velocity if item.actuator_name == "upper_eyelids" else 0.0
                ),
            )
            for item in target.targets
        ),
        filtered_intent=_intent(accepted_ns=generated_ns),
        latent_vector=(0.1,) * residual.config.hidden_size,
        numpy_rng_state=rng.bit_generator.state,
        torch_rng_state=tuple(int(value) for value in torch.random.get_rng_state()),
        event_history=(),
        model_id="moving-blink-test-v1",
        model_sha256=SHA256_ZERO,
        calibration_sha256=controller_config.calibration_sha256,
        controller_settings_sha256=controller_config.controller_settings_sha256,
        monotonic_ns=generated_ns,
    )
    blink = FaceEvent(
        schema_version="face-event/v1",
        event_id="recorded-moving-blink",
        model_id=face.config.model_id,
        model_sha256=face.model_sha256,
        kind=FaceEventKind.BLINK,
        starts_monotonic_ns=600_000_000,
        onset_s=0.6,
        hold_s=0.2,
        release_s=0.6,
        amplitude=abs(requested_target),
        actuator_names=("lower_eyelids", "upper_eyelids"),
        peak_targets=(
            ActuatorTarget(
                actuator_name="lower_eyelids",
                normalized_position=requested_target,
            ),
            ActuatorTarget(
                actuator_name="upper_eyelids",
                normalized_position=requested_target,
            ),
        ),
    )
    state = FaceEventState(
        schema_version="face-event-state/v1",
        model_id=face.config.model_id,
        model_sha256=face.model_sha256,
        monotonic_ns=generated_ns,
        planned_through_ns=2_000_000_000,
        history=(blink,),
    ).to_generator_state(state)
    moving = ControllerState(
        schema_version="controller-state/v1",
        actuator_name="upper_eyelids",
        calibration_sha256=controller_config.calibration_sha256,
        position=moving_position,
        velocity=moving_velocity,
    )
    with pytest.raises(ValueError, match="stopping distance"):
        response.predict(
            moving,
            ProductionCandidateComposer._update(
                0.2, {"upper_eyelids": requested_target}
            ),
            elapsed_s=0.2,
        )

    plan = composer.plan(
        _intent(accepted_ns=generated_ns),
        state,
        horizon_s=1.0,
        prefix_duration_s=0.4,
        generated_monotonic_ns=generated_ns,
    )

    expected = response.predict(
        moving,
        ProductionCandidateComposer._update(0.2, {"upper_eyelids": 0.0}),
        elapsed_s=0.2,
    )
    first_fallback = next(
        target
        for target in plan.proposal.horizon.updates[1].targets
        if target.actuator_name == "upper_eyelids"
    )
    assert plan.proposal.support_status == "fallback"
    assert first_fallback.normalized_position == pytest.approx(expected.position)
    assert plan.boundary_state.numpy_rng_state == state.numpy_rng_state
    assert plan.boundary_state.latent_vector == state.latent_vector
    assert plan.boundary_state.torch_rng_state == state.torch_rng_state
    assert not any(
        record.event_type == "head-decision-state/v1"
        for record in plan.boundary_state.event_history
    )


def test_nod_preserves_residual_displacement_on_inactive_head_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting a nod must not turn residual yaw into an implicit head reset."""

    controller_config = load_controller_response_config(CONTROLLER_CONFIG_PATH)
    response = ControllerResponse(config=controller_config)
    anchor_config = load_procedural_motion_config(CONFIG_PATH)
    residual = ResidualStateSpace(
        load_residual_state_space_config(RESIDUAL_CONFIG_PATH)
    )
    with torch.no_grad():
        for parameter in residual.parameters():
            parameter.zero_()
        yaw_index = residual.config.actuator_names.index("neck_rotation")
        residual.residual_head.bias[yaw_index] = 0.5
    face = FaceEventGenerator(
        config=load_face_event_config(FACE_CONFIG_PATH),
        controller_config=controller_config,
    )
    head = HeadGestureScheduler(
        config=load_head_gesture_config(HEAD_CONFIG_PATH),
        controller_config=controller_config,
    )
    composer = ProductionCandidateComposer(
        anchor_planner=AnchorPlanner(config=anchor_config),
        residual_model=residual,
        face_events=face,
        head_scheduler=head,
        controller_response=response,
    )
    neutral = anchor_config.anchor("neutral")
    target = TargetUpdate(offset_s=0.0, targets=neutral.targets)
    intent = _intent().model_copy(update={"intensity": 0.0})
    rng = np.random.default_rng(19)
    state = GeneratorState(
        schema_version="generator-state/v1",
        last_accepted_target=target,
        last_reported_pose=target,
        estimated_velocity=tuple(
            ActuatorVelocity(actuator_name=item.actuator_name, velocity_per_s=0.0)
            for item in target.targets
        ),
        filtered_intent=intent,
        latent_vector=(0.0,) * residual.config.hidden_size,
        numpy_rng_state=rng.bit_generator.state,
        torch_rng_state=tuple(int(value) for value in torch.random.get_rng_state()),
        event_history=(
            EventHistoryRecord(
                event_type="head-decision-state/v1",
                started_monotonic_ns=0,
                ended_monotonic_ns=0,
                payload={"model_sha256": head.model_sha256},
            ),
        ),
        model_id="residual-before-head-test-v1",
        model_sha256=SHA256_ZERO,
        calibration_sha256=controller_config.calibration_sha256,
        controller_settings_sha256=controller_config.controller_settings_sha256,
        monotonic_ns=0,
    )

    residual_plan = composer.plan(
        intent,
        state,
        horizon_s=1.0,
        prefix_duration_s=1.0,
        generated_monotonic_ns=0,
    )
    residual_yaw = next(
        target.normalized_position
        for target in residual_plan.boundary_state.last_accepted_target.targets
        if target.actuator_name == "neck_rotation"
    )
    assert residual_yaw != 0.0
    state_values = residual_plan.boundary_state.model_dump()
    accepted_targets = state_values["last_accepted_target"]["targets"]
    state_values["last_accepted_target"]["targets"] = tuple(
        reversed(accepted_targets)
    )
    residual_state = GeneratorState.model_validate(state_values)
    policy = head.config.policy(HeadGestureKind.NOD)
    nod = HeadGesture(
        schema_version="head-gesture/v1",
        gesture_id="nod-after-residual",
        model_id=head.config.model_id,
        model_sha256=head.model_sha256,
        kind=HeadGestureKind.NOD,
        starts_monotonic_ns=1_000_000_000,
        actuator_name=policy.actuator_name,
        amplitude=policy.amplitude.minimum,
        duration_s=policy.duration_s.minimum,
        cycles=policy.cycles.minimum,
        asymmetry=policy.asymmetry.minimum,
        hold_s=policy.hold_s.minimum,
        recovery_s=policy.recovery_s.minimum,
        recovery_targets=head.config.recovery_targets,
    )
    monkeypatch.setattr(head, "sample", lambda *args, **kwargs: nod)

    gesture_plan = composer.plan(
        intent.model_copy(update={"accepted_monotonic_ns": 1_000_000_000}),
        residual_state,
        horizon_s=1.0,
        prefix_duration_s=0.4,
        generated_monotonic_ns=1_000_000_000,
    )

    assert gesture_plan.proposal.support_status == "supported"
    assert all(
        next(
            target.normalized_position
            for target in update.targets
            if target.actuator_name == "neck_rotation"
        )
        == residual_yaw
        for update in gesture_plan.proposal.horizon.updates
    )


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
        persisted_affect = state.filtered_intent.intensity + sum(
            (index + 1) * value
            for index, value in enumerate(state.filtered_intent.vector)
        )
        incoming_affect = intent.intensity + sum(
            (index + 1) * value for index, value in enumerate(intent.vector)
        )
        delta = (
            float(rng.uniform(0.001, 0.005))
            + 0.001 * persisted_affect
            + 0.001 * incoming_affect
        )

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
                        payload={
                            "ordinal": len(state.event_history),
                            "persisted_intensity": state.filtered_intent.intensity,
                            "incoming_intensity": intent.intensity,
                        },
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
    incoming = _intent().model_copy(
        update={"vector": (0.2, -0.1, 0.3), "intensity": 0.5}
    )
    runtime = StreamingMotionGenerator(
        generator=_StatefulCandidate(),
        horizon_s=1.0,
        prefix_duration_s=0.4,
    )

    _, boundary = runtime.replan(incoming, initial, 0)
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
    assert restored.filtered_intent == boundary.filtered_intent == incoming
    assert replayed == direct


def test_stateful_candidate_continuation_depends_on_persisted_filtered_intent() -> None:
    """Ignoring persisted affect would make valid state corruption undetectable."""

    _, config = _runtime()
    initial = _state(config)
    state_values = initial.model_dump()
    state_values["filtered_intent"] = initial.filtered_intent.model_copy(
        update={"vector": (0.3, -0.2, 0.1), "intensity": 0.4}
    )
    altered = GeneratorState.model_validate(state_values)
    runtime = StreamingMotionGenerator(
        generator=_StatefulCandidate(),
        horizon_s=1.0,
        prefix_duration_s=0.4,
    )

    baseline_prefix, baseline_state = runtime.replan(_intent(), initial, 0)
    mutated_prefix, mutated_state = runtime.replan(_intent(), altered, 0)

    assert mutated_prefix != baseline_prefix
    assert mutated_state != baseline_state


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


def test_short_prefix_stream_retains_blink_and_gaze_timers() -> None:
    """Restarting relative timers at each prefix permanently starves eye events."""

    runtime, config = _runtime()
    state = _state(config)
    eye_names = {
        "upper_eyelids",
        "lower_eyelids",
        "right_eye_horizontal",
        "left_eye_horizontal",
    }
    moved = set()
    for _ in range(150):
        prefix, state = runtime.replan(
            _intent(accepted_ns=state.monotonic_ns), state, state.monotonic_ns
        )
        moved.update(
            target.actuator_name
            for update in prefix.updates
            for target in update.targets
            if abs(target.normalized_position) > 1e-9
        )
    assert eye_names <= moved


class _SparseProposalGenerator:
    """Complete initial pose followed by independently changing channels."""

    def step(
        self,
        intent: FilteredIntent,
        state: TargetUpdate,
        seed: int,
        horizon_s: float,
        *,
        generated_monotonic_ns: int,
    ) -> MotionProposal:
        config = load_procedural_motion_config(CONFIG_PATH)
        proposal = ProceduralMotionGenerator(
            config=config, anchor_planner=AnchorPlanner(config=config)
        ).step(
            intent,
            state,
            seed,
            horizon_s,
            generated_monotonic_ns=generated_monotonic_ns,
        )
        return proposal.model_copy(
            update={
                "horizon": TargetUpdateHorizon(
                    schema_version="target-update-horizon/v1",
                    updates=(
                        state,
                        TargetUpdate(
                            offset_s=0.2,
                            targets=(
                                ActuatorTarget(
                                    actuator_name="mouth_open",
                                    normalized_position=0.1,
                                ),
                            ),
                        ),
                        TargetUpdate(
                            offset_s=0.4,
                            targets=(
                                ActuatorTarget(
                                    actuator_name="neck_rotation",
                                    normalized_position=0.2,
                                ),
                            ),
                        ),
                    ),
                )
            }
        )


class _SparseCandidate:
    """Supply a valid boundary independently of the procedural adapter."""

    def plan(
        self,
        intent: FilteredIntent,
        state: GeneratorState,
        horizon_s: float,
        prefix_duration_s: float,
        *,
        generated_monotonic_ns: int,
    ) -> CandidatePlan:
        proposal = _SparseProposalGenerator().step(
            intent,
            state.last_accepted_target,
            1,
            horizon_s,
            generated_monotonic_ns=generated_monotonic_ns,
        )
        boundary = TargetUpdate(
            offset_s=0.0,
            targets=tuple(
                target.model_copy(
                    update={
                        "normalized_position": {
                            "mouth_open": 0.1,
                            "neck_rotation": 0.2,
                        }.get(target.actuator_name, target.normalized_position)
                    }
                )
                for target in state.last_accepted_target.targets
            ),
        )
        values = state.model_dump()
        values.update(
            last_accepted_target=boundary,
            filtered_intent=intent,
            monotonic_ns=generated_monotonic_ns + round(prefix_duration_s * 1e9),
        )
        return CandidatePlan(
            proposal=proposal, boundary_state=GeneratorState.model_validate(values)
        )


@pytest.mark.parametrize("through_adapter", [False, True])
def test_sparse_prefix_accumulates_all_accepted_targets(through_adapter: bool) -> None:
    """Treating the last sparse delta as a pose loses earlier accepted targets."""

    _, config = _runtime()
    initial = _state(config)
    runtime = StreamingMotionGenerator(
        generator=(
            ProceduralCandidateGenerator(generator=_SparseProposalGenerator())
            if through_adapter
            else _SparseCandidate()
        ),
        horizon_s=0.4,
        prefix_duration_s=0.4,
    )
    _, boundary = runtime.replan(_intent(), initial, 0)
    expected = {
        target.actuator_name: target.normalized_position
        for target in initial.last_accepted_target.targets
    }
    expected.update(mouth_open=0.1, neck_rotation=0.2)
    assert {
        target.actuator_name: target.normalized_position
        for target in boundary.last_accepted_target.targets
    } == expected
    assert load_state(dump_state(boundary)) == boundary


def test_procedural_prefixes_follow_uninterrupted_drift_and_events() -> None:
    """Resampling drift or restarting event clocks changes the absolute trajectory."""

    runtime, config = _runtime()
    initial = _state(config)
    generator = ProceduralCandidateGenerator(
        generator=ProceduralMotionGenerator(
            config=config,
            anchor_planner=AnchorPlanner(config=config),
        )
    )
    reference = generator.plan(
        _intent(),
        initial,
        12.0,
        0.4,
        generated_monotonic_ns=0,
    )
    expected = {
        round(update.offset_s * 1e9): update
        for update in reference.proposal.horizon.updates
    }
    state = initial
    for _ in range(30):
        prefix, state = runtime.replan(
            _intent(accepted_ns=state.monotonic_ns),
            state,
            state.monotonic_ns,
        )
        for update in prefix.updates:
            reference_update = expected[
                prefix.starts_at_ns + round(update.offset_s * 1e9)
            ]
            assert {t.actuator_name: t.normalized_position for t in update.targets} == (
                pytest.approx(
                    {
                        t.actuator_name: t.normalized_position
                        for t in reference_update.targets
                    },
                    abs=1e-12,
                )
            )


def test_procedural_lookahead_and_restore_preserve_intent_transitions() -> None:
    """Speculative events or process-local state must not affect accepted replay."""

    short, config = _runtime()
    generator = ProceduralCandidateGenerator(
        generator=ProceduralMotionGenerator(
            config=config,
            anchor_planner=AnchorPlanner(config=config),
        )
    )
    long = StreamingMotionGenerator(
        generator=generator, horizon_s=12.0, prefix_duration_s=0.4
    )
    direct_state = _state(config)
    restored_state = load_state(dump_state(direct_state))
    for index in range(40):
        intent = _intent(accepted_ns=direct_state.monotonic_ns).model_copy(
            update={
                "support_status": SupportStatus.FALLBACK
                if 12 <= index < 17
                else SupportStatus.SUPPORTED,
                "intensity": 0.2 if index >= 17 else 0.7,
            }
        )
        before = dump_state(restored_state)
        generator.plan(
            intent,
            restored_state,
            24.0,
            0.4,
            generated_monotonic_ns=restored_state.monotonic_ns,
        )
        assert dump_state(restored_state) == before
        direct_prefix, direct_state = short.replan(
            intent,
            direct_state,
            direct_state.monotonic_ns,
        )
        restored_prefix, restored_state = long.replan(
            intent,
            restored_state,
            restored_state.monotonic_ns,
        )
        assert restored_prefix.updates == direct_prefix.updates
        assert restored_state == direct_state
        restored_state = load_state(dump_state(restored_state))


@pytest.mark.parametrize(
    "corruption", ["future_boundary", "ended_event", "phase", "rng", "channels"]
)
def test_restore_rejects_invalid_procedural_continuation(corruption: str) -> None:
    """Invalid persisted schedule data must fail before motion generation."""

    runtime, config = _runtime()
    _, boundary = runtime.replan(_intent(), _state(config), 0)
    payload = boundary.model_dump()
    continuation = payload["procedural_continuation"]
    if corruption == "future_boundary":
        continuation["monotonic_ns"] += 1
    elif corruption == "ended_event":
        continuation["monotonic_ns"] = 20_000_000_000
        payload["monotonic_ns"] = 20_000_000_000
    elif corruption == "phase":
        continuation["drift"]["head_tilt"] = ((0.1, float("nan")),)
    elif corruption == "rng":
        continuation["numpy_rng_state"] = {"bit_generator": "PCG64"}
    else:
        del continuation["applied_variation"]["mouth_open"]
    with pytest.raises(ValueError):
        GeneratorState.model_validate(payload)
