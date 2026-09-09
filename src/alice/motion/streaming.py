"""Overlapping-horizon acceptance for hardware-independent motion proposals."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Literal, Protocol

import numpy as np
import torch
from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.affect import MonotonicNanoseconds
from alice.contracts.blendshapes import NonEmptyString
from alice.contracts.motion import MotionProposal, TargetUpdate, TargetUpdateHorizon
from alice.models.head_scheduler import HeadGestureScheduler
from alice.models.residual_state_space import ResidualStateSpace
from alice.motion.anchors import AnchorPlanner
from alice.motion.controller_response import ControllerResponse, ControllerState
from alice.motion.face_events import FaceEvent, FaceEventGenerator
from alice.motion.head_primitives import HeadGesture
from alice.motion.intent_filter import FilteredIntent
from alice.motion.procedural import ProceduralMotionGenerator
from alice.motion.state import EventHistoryRecord, GeneratorState


class _CandidateGenerator(Protocol):
    def plan(
        self,
        intent: FilteredIntent,
        state: GeneratorState,
        horizon_s: float,
        prefix_duration_s: float,
        *,
        generated_monotonic_ns: int,
    ) -> CandidatePlan: ...


class _ProposalGenerator(Protocol):
    def step(
        self,
        intent: FilteredIntent,
        state: TargetUpdate,
        seed: int,
        horizon_s: float,
        *,
        generated_monotonic_ns: int,
    ) -> MotionProposal: ...


class CandidatePlan(BaseModel):
    """Candidate horizon and complete state at its accepted-prefix boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal: MotionProposal
    boundary_state: GeneratorState


class _InfeasibleCandidateError(ValueError):
    """A composed event or residual command cannot be realized safely."""


class ProceduralCandidateGenerator:
    """Adapt a seeded proposal generator to the explicit stateful protocol."""

    def __init__(self, *, generator: _ProposalGenerator) -> None:
        self._generator = generator

    def plan(
        self,
        intent: FilteredIntent,
        state: GeneratorState,
        horizon_s: float,
        prefix_duration_s: float,
        *,
        generated_monotonic_ns: int,
    ) -> CandidatePlan:
        rng = _restore_numpy_rng(state.numpy_rng_state)
        continuation = state.procedural_continuation
        seed = (
            continuation.seed
            if continuation is not None
            else int(rng.integers(0, np.iinfo(np.int64).max))
        )
        if isinstance(self._generator, ProceduralMotionGenerator):
            proposal, continuation = self._generator.step_continuation(
                intent,
                state.last_accepted_target,
                seed,
                horizon_s,
                prefix_duration_s=prefix_duration_s,
                generated_monotonic_ns=generated_monotonic_ns,
                continuation=continuation,
            )
        else:
            proposal = self._generator.step(
                intent,
                state.last_accepted_target,
                seed,
                horizon_s,
                generated_monotonic_ns=generated_monotonic_ns,
            )
        accepted_updates = tuple(
            update
            for update in proposal.horizon.updates
            if update.offset_s <= prefix_duration_s
        )
        if not accepted_updates:
            raise ValueError("candidate has no updates inside the accepted prefix")

        boundary_target = _accumulate_targets(
            state.last_accepted_target, accepted_updates
        )
        ends_at_ns = generated_monotonic_ns + round(prefix_duration_s * 1_000_000_000)
        state_values = state.model_dump()
        state_values.update(
            {
                "last_accepted_target": boundary_target,
                "filtered_intent": intent,
                "numpy_rng_state": rng.bit_generator.state,
                "procedural_continuation": continuation,
                "monotonic_ns": ends_at_ns,
            }
        )
        return CandidatePlan(
            proposal=proposal,
            boundary_state=GeneratorState.model_validate(state_values),
        )


class ProductionCandidateComposer:
    """Compose anchor, residual, sparse events, gestures, and response state.

    Merge precedence is explicit: the bounded residual augments the anchor;
    face events then override their coupled facial channels; an active head
    primitive overrides its semantic head axis. The controller model realizes
    the merged commands before they enter a proposal.
    """

    def __init__(
        self,
        *,
        anchor_planner: AnchorPlanner,
        residual_model: ResidualStateSpace,
        face_events: FaceEventGenerator,
        head_scheduler: HeadGestureScheduler,
        controller_response: ControllerResponse,
    ) -> None:
        self._anchor = anchor_planner
        self._residual = residual_model.eval()
        self._face = face_events
        self._head = head_scheduler
        self._response = controller_response
        names = residual_model.config.actuator_names
        if names != anchor_planner.config.semantic_actuator_names:
            raise ValueError("anchor and residual actuator identities mismatch")
        if (
            tuple(a.actuator_name for a in controller_response.config.actuators)
            != names
        ):
            raise ValueError("residual and controller actuator identities mismatch")

    def plan(
        self,
        intent: FilteredIntent,
        state: GeneratorState,
        horizon_s: float,
        prefix_duration_s: float,
        *,
        generated_monotonic_ns: int,
    ) -> CandidatePlan:
        conservative = intent.support_status.value in {"fallback", "stale"}
        if conservative:
            return self._compose(
                intent,
                state,
                horizon_s,
                prefix_duration_s,
                generated_monotonic_ns=generated_monotonic_ns,
                conservative=True,
                project_infeasible=True,
            )
        try:
            return self._compose(
                intent,
                state,
                horizon_s,
                prefix_duration_s,
                generated_monotonic_ns=generated_monotonic_ns,
                conservative=False,
                project_infeasible=False,
            )
        except _InfeasibleCandidateError:
            return self._compose(
                intent,
                state,
                horizon_s,
                prefix_duration_s,
                generated_monotonic_ns=generated_monotonic_ns,
                conservative=True,
                project_infeasible=True,
            )

    def _compose(
        self,
        intent: FilteredIntent,
        state: GeneratorState,
        horizon_s: float,
        prefix_duration_s: float,
        *,
        generated_monotonic_ns: int,
        conservative: bool,
        project_infeasible: bool,
    ) -> CandidatePlan:
        rng = _restore_numpy_rng(state.numpy_rng_state)
        torch_rng = torch.tensor(state.torch_rng_state, dtype=torch.uint8)
        try:
            with torch.random.fork_rng(devices=[]):
                torch.random.set_rng_state(torch_rng)
        except RuntimeError as error:
            raise ValueError("invalid Torch RNG state") from error
        fallback_intent = intent.support_status.value in {"fallback", "stale"}
        anchor = (
            self._anchor.plan_neutral(state.last_accepted_target, horizon_s)
            if fallback_intent
            else self._anchor.plan(intent, state.last_accepted_target, horizon_s)
        )
        face_state = self._face.state_from(state)
        sampled_face = (
            ()
            if conservative
            else self._face.sample(intent, face_state, rng, horizon_s)
        )
        history = self._head.compact_history(
            state.event_history, at_ns=generated_monotonic_ns
        )

        active_head = (
            None
            if conservative
            else self._active_head(history, generated_monotonic_ns)
        )
        if (
            not conservative
            and active_head is None
            and self._head.decision_due(
                history, generated_monotonic_ns=generated_monotonic_ns
            )
        ):
            active_head = self._head.sample(
                intent,
                history,
                rng,
                generated_monotonic_ns=generated_monotonic_ns,
            )
            history = self._head.record_decision(
                history, generated_monotonic_ns=generated_monotonic_ns
            )
            if active_head is not None:
                accepted_positions = self._positions(state.last_accepted_target)
                head_targets = tuple(
                    ActuatorTarget(
                        actuator_name=actuator_name,
                        normalized_position=accepted_positions[actuator_name],
                    )
                    for actuator_name in self._head.config.semantics.actuator_names
                )
                active_head = active_head.model_copy(
                    update={"initial_targets": head_targets}
                )
                history = self._head.record(history, active_head)

        head_horizon = None
        if active_head is not None:
            head_horizon = self._head.primitives.render_window(
                active_head,
                window_start_ns=generated_monotonic_ns,
                horizon_s=horizon_s,
            )

        hidden_size = self._residual.config.hidden_size
        latent = torch.tensor(state.latent_vector, dtype=torch.float32)
        hidden = (
            latent.reshape(1, 1, hidden_size)
            if latent.numel() == hidden_size
            else torch.zeros((1, 1, hidden_size), dtype=torch.float32)
        )
        response_states = {
            item.actuator_name: ControllerState(
                schema_version="controller-state/v1",
                actuator_name=item.actuator_name,
                calibration_sha256=state.calibration_sha256,
                position=next(
                    t.normalized_position
                    for t in state.last_reported_pose.targets
                    if t.actuator_name == item.actuator_name
                ),
                velocity=item.velocity_per_s,
            )
            for item in state.estimated_velocity
        }
        updates: list[TargetUpdate] = []
        previous_s = 0.0
        boundary_hidden = hidden
        boundary_velocities = {
            name: item.velocity for name, item in response_states.items()
        }
        with torch.no_grad():
            for index, base in enumerate(anchor.updates):
                anchor_values = self._positions(base)
                if index == 0:
                    updates.append(state.last_accepted_target)
                    continue
                elapsed = base.offset_s - previous_s
                previous_s = base.offset_s
                ordered_anchor = torch.tensor(
                    [
                        [
                            list(
                                anchor_values[name]
                                for name in self._residual.config.actuator_names
                            )
                        ]
                    ],
                    dtype=torch.float32,
                )
                positions = torch.tensor(
                    [
                        [
                            [
                                response_states[name].position
                                for name in self._residual.config.actuator_names
                            ]
                        ]
                    ],
                    dtype=torch.float32,
                )
                velocities = torch.tensor(
                    [
                        [
                            [
                                response_states[name].velocity
                                for name in self._residual.config.actuator_names
                            ]
                        ]
                    ],
                    dtype=torch.float32,
                )
                features = self._residual.compose_features(
                    affect=torch.tensor([[list(intent.vector)]], dtype=torch.float32),
                    intensity=torch.tensor([[[intent.intensity]]], dtype=torch.float32),
                    anchor_pose=ordered_anchor,
                    response_position=positions,
                    response_velocity=velocities,
                    elapsed_s=torch.tensor([[[elapsed]]], dtype=torch.float32),
                )
                residual, hidden = self._residual(features, hidden)
                if conservative:
                    residual = torch.zeros_like(residual)
                commands = {
                    name: max(
                        -1.0, min(1.0, anchor_values[name] + float(residual[0, 0, i]))
                    )
                    for i, name in enumerate(self._residual.config.actuator_names)
                }
                absolute_ns = generated_monotonic_ns + round(base.offset_s * 1e9)
                self._merge_face(commands, sampled_face, absolute_ns)
                if head_horizon is not None:
                    commands.update(
                        self._positions_at(head_horizon.updates, base.offset_s)
                    )
                command_update = self._update(base.offset_s, commands)
                if not self._commands_feasible(response_states, command_update):
                    if not project_infeasible:
                        raise _InfeasibleCandidateError(
                            "composed command is not controller-feasible"
                        )
                    command_update = self._project_feasible_commands(
                        response_states, command_update
                    )
                for name, controller_state in tuple(response_states.items()):
                    response_states[name] = self._response.predict(
                        controller_state, command_update, elapsed
                    )
                updates.append(
                    self._update(
                        base.offset_s,
                        {name: item.position for name, item in response_states.items()},
                    )
                )
                if base.offset_s <= prefix_duration_s:
                    boundary_hidden = hidden.clone()
                    boundary_velocities = {
                        name: item.velocity for name, item in response_states.items()
                    }

        horizon = TargetUpdateHorizon(
            schema_version="target-update-horizon/v1", updates=tuple(updates)
        )
        seed = int(rng.integers(0, np.iinfo(np.int64).max))
        proposal = MotionProposal(
            schema_version="motion-proposal/v1",
            proposal_id=f"composed-{generated_monotonic_ns}-{seed}",
            run_id=f"composed-{intent.source_id}",
            generated_monotonic_ns=generated_monotonic_ns,
            expires_monotonic_ns=generated_monotonic_ns + round(horizon_s * 1e9) + 1,
            seed=seed,
            model_id=state.model_id,
            model_sha256=state.model_sha256,
            calibration_sha256=state.calibration_sha256,
            controller_settings_sha256=state.controller_settings_sha256,
            horizon=horizon,
            support_status=(
                "fallback"
                if conservative and not fallback_intent
                else intent.support_status.value
            ),
        )
        accepted = [u for u in horizon.updates if u.offset_s <= prefix_duration_s]
        boundary = accepted[-1].model_copy(update={"offset_s": 0.0})
        ends_ns = generated_monotonic_ns + round(prefix_duration_s * 1e9)
        state_values = state.model_dump()
        state_values.update(
            last_accepted_target=boundary,
            last_reported_pose=boundary,
            estimated_velocity=tuple(
                {"actuator_name": name, "velocity_per_s": boundary_velocities[name]}
                for name in self._residual.config.actuator_names
            ),
            filtered_intent=intent,
            latent_vector=(
                state.latent_vector
                if conservative
                else tuple(float(v) for v in boundary_hidden.reshape(-1))
            ),
            numpy_rng_state=(
                state.numpy_rng_state
                if conservative
                else rng.bit_generator.state
            ),
            event_history=history,
            monotonic_ns=ends_ns,
        )
        next_state = GeneratorState.model_validate(state_values)
        planned_through_ns = (
            max(face_state.planned_through_ns, ends_ns)
            if conservative
            else generated_monotonic_ns + round(horizon_s * 1e9)
        )
        advanced_face = face_state.advance(
            sampled_face,
            monotonic_ns=ends_ns,
            planned_through_ns=planned_through_ns,
        )
        next_state = self._face.compact_state(advanced_face).to_generator_state(
            next_state
        )
        return CandidatePlan(proposal=proposal, boundary_state=next_state)

    def _commands_feasible(
        self,
        states: Mapping[str, ControllerState],
        update: TargetUpdate,
    ) -> bool:
        return all(
            self._response.is_feasible(state, update) for state in states.values()
        )

    def _project_feasible_commands(
        self,
        states: Mapping[str, ControllerState],
        update: TargetUpdate,
    ) -> TargetUpdate:
        positions = self._positions(update)
        for name, state in states.items():
            if self._response.is_feasible(state, update):
                continue
            parameters = self._response.config.actuator(name)
            stopping_distance = state.velocity**2 / (
                2.0 * parameters.max_acceleration_per_s2
            )
            direction = math.copysign(1.0, state.velocity)
            target = state.position + direction * stopping_distance
            while direction * (target - state.position) < stopping_distance:
                target = math.nextafter(target, direction * math.inf)
            if not -1.0 <= target <= 1.0:
                raise ValueError(
                    f"controller state for {name!r} cannot stop inside normalized range"
                )
            positions[name] = target
            projected = self._update(update.offset_s, positions)
            if not self._response.is_feasible(state, projected):
                raise RuntimeError(
                    f"failed to project controller-feasible fallback for {name!r}"
                )
            update = projected
        return update

    @staticmethod
    def _positions(update: TargetUpdate) -> dict[str, float]:
        return {t.actuator_name: t.normalized_position for t in update.targets}

    @staticmethod
    def _update(offset_s: float, positions: Mapping[str, float]) -> TargetUpdate:
        return TargetUpdate(
            offset_s=offset_s,
            targets=tuple(
                ActuatorTarget(actuator_name=name, normalized_position=value)
                for name, value in positions.items()
            ),
        )

    @staticmethod
    def _positions_at(
        updates: tuple[TargetUpdate, ...], at_s: float
    ) -> dict[str, float]:
        chosen = updates[0]
        for update in updates:
            if update.offset_s > at_s:
                break
            chosen = update
        return ProductionCandidateComposer._positions(chosen)

    @staticmethod
    def _merge_face(
        commands: dict[str, float], events: tuple[FaceEvent, ...], at_ns: int
    ) -> None:
        for event in events:
            if not event.starts_monotonic_ns <= at_ns <= event.ends_monotonic_ns:
                continue
            elapsed = (at_ns - event.starts_monotonic_ns) / 1e9
            if elapsed < event.onset_s:
                phase = elapsed / event.onset_s
            elif elapsed <= event.onset_s + event.hold_s:
                phase = 1.0
            else:
                phase = 1.0 - (elapsed - event.onset_s - event.hold_s) / event.release_s
            envelope = max(0.0, min(1.0, phase))
            for target in event.peak_targets:
                base = commands[target.actuator_name]
                commands[target.actuator_name] = max(
                    -1.0, min(1.0, base + envelope * target.normalized_position)
                )

    @staticmethod
    def _active_head(
        history: tuple[EventHistoryRecord, ...], at_ns: int
    ) -> HeadGesture | None:
        gestures = []
        for record in history:
            if getattr(record, "event_type", None) == "head-gesture/v1":
                gesture = HeadGesture.from_history_record(record)
                if gesture.starts_monotonic_ns <= at_ns < gesture.ends_monotonic_ns:
                    gestures.append(gesture)
        return gestures[-1] if gestures else None


class AcceptedPrefix(BaseModel):
    """The bounded prefix accepted from one longer motion proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["accepted-prefix/v1"]
    proposal_id: NonEmptyString
    starts_at_ns: MonotonicNanoseconds
    ends_at_ns: MonotonicNanoseconds
    updates: tuple[TargetUpdate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_window(self) -> AcceptedPrefix:
        if self.ends_at_ns <= self.starts_at_ns:
            raise ValueError("accepted prefix must have positive duration")
        if self.updates[0].offset_s != 0.0:
            raise ValueError("accepted prefix must begin at offset zero")
        duration_s = (self.ends_at_ns - self.starts_at_ns) / 1_000_000_000
        if any(update.offset_s > duration_s for update in self.updates):
            raise ValueError("accepted prefix update exceeds its time window")
        return self


class StreamingMotionGenerator:
    """Generate full candidates while accepting only a short continuous prefix."""

    def __init__(
        self,
        *,
        generator: _CandidateGenerator,
        horizon_s: float,
        prefix_duration_s: float,
    ) -> None:
        if not math.isfinite(horizon_s) or horizon_s <= 0.0:
            raise ValueError("horizon_s must be finite and positive")
        if not math.isfinite(prefix_duration_s) or prefix_duration_s <= 0.0:
            raise ValueError("prefix_duration_s must be finite and positive")
        if prefix_duration_s > horizon_s:
            raise ValueError("prefix duration must not exceed candidate horizon")
        prefix_duration_ns = round(prefix_duration_s * 1_000_000_000)
        if prefix_duration_ns <= 0:
            raise ValueError("prefix duration must cover at least one nanosecond")

        self._generator = generator
        self._horizon_s = horizon_s
        self._prefix_duration_s = prefix_duration_s
        self._prefix_duration_ns = prefix_duration_ns

    def replan(
        self,
        intent: FilteredIntent,
        state: GeneratorState,
        now_ns: int,
    ) -> tuple[AcceptedPrefix, GeneratorState]:
        """Accept a continuous prefix and return its complete continuation state."""

        if now_ns < state.monotonic_ns:
            raise ValueError("now_ns must not precede persisted generator state")
        if intent.accepted_monotonic_ns > now_ns:
            raise ValueError("filtered intent postdates replan time")
        if intent.accepted_monotonic_ns < state.filtered_intent.accepted_monotonic_ns:
            raise ValueError("filtered intent timestamp regressed")
        if intent.affect_schema_id != state.filtered_intent.affect_schema_id:
            raise ValueError("filtered intent schema identity is incompatible")
        if len(intent.vector) != len(state.filtered_intent.vector):
            raise ValueError("filtered intent dimension count is incompatible")

        plan = self._generator.plan(
            intent,
            state,
            self._horizon_s,
            self._prefix_duration_s,
            generated_monotonic_ns=now_ns,
        )
        proposal = plan.proposal
        self._validate_candidate(proposal, state=state, now_ns=now_ns)

        updates = tuple(
            update
            for update in proposal.horizon.updates
            if update.offset_s <= self._prefix_duration_s
        )
        if not updates:
            raise ValueError("candidate has no updates inside the accepted prefix")

        ends_at_ns = now_ns + self._prefix_duration_ns
        prefix = AcceptedPrefix(
            schema_version="accepted-prefix/v1",
            proposal_id=proposal.proposal_id,
            starts_at_ns=now_ns,
            ends_at_ns=ends_at_ns,
            updates=updates,
        )
        self._validate_boundary_state(
            plan.boundary_state,
            previous=state,
            intent=intent,
            proposal=proposal,
            accepted_updates=updates,
            ends_at_ns=ends_at_ns,
        )
        return prefix, plan.boundary_state

    def _validate_candidate(
        self,
        proposal: MotionProposal,
        *,
        state: GeneratorState,
        now_ns: int,
    ) -> None:
        if proposal.generated_monotonic_ns != now_ns:
            raise ValueError("candidate generation time does not match replan time")
        if proposal.expires_monotonic_ns <= now_ns + self._prefix_duration_ns:
            raise ValueError("candidate expires before the accepted prefix ends")
        if proposal.horizon.updates[-1].offset_s < self._prefix_duration_s:
            raise ValueError("candidate horizon does not cover accepted prefix")
        identities = (
            ("model identity", proposal.model_id, state.model_id),
            ("model identity", proposal.model_sha256, state.model_sha256),
            (
                "calibration identity",
                proposal.calibration_sha256,
                state.calibration_sha256,
            ),
            (
                "controller identity",
                proposal.controller_settings_sha256,
                state.controller_settings_sha256,
            ),
        )
        for label, candidate_value, state_value in identities:
            if candidate_value != state_value:
                raise ValueError(f"candidate {label} changed")

        first = proposal.horizon.updates[0]
        if first.offset_s != 0.0 or StreamingMotionGenerator._positions(
            first
        ) != StreamingMotionGenerator._positions(state.last_accepted_target):
            raise ValueError("discontinuous candidate start")

    @staticmethod
    def _positions(update: TargetUpdate) -> dict[str, float]:
        return {
            target.actuator_name: target.normalized_position
            for target in update.targets
        }

    @classmethod
    def _validate_boundary_state(
        cls,
        boundary: GeneratorState,
        *,
        previous: GeneratorState,
        intent: FilteredIntent,
        proposal: MotionProposal,
        accepted_updates: tuple[TargetUpdate, ...],
        ends_at_ns: int,
    ) -> None:
        if boundary.monotonic_ns != ends_at_ns:
            raise ValueError("candidate boundary time does not match prefix end")
        if boundary.filtered_intent != intent:
            raise ValueError("candidate boundary did not retain filtered intent")
        expected_target = _accumulate_targets(
            previous.last_accepted_target, accepted_updates
        )
        if cls._positions(boundary.last_accepted_target) != cls._positions(
            expected_target
        ):
            raise ValueError("candidate boundary target does not match accepted prefix")

        identities = (
            ("model identity", boundary.model_id, previous.model_id),
            ("model identity", boundary.model_sha256, proposal.model_sha256),
            (
                "calibration identity",
                boundary.calibration_sha256,
                proposal.calibration_sha256,
            ),
            (
                "controller identity",
                boundary.controller_settings_sha256,
                proposal.controller_settings_sha256,
            ),
        )
        for label, boundary_value, expected_value in identities:
            if boundary_value != expected_value:
                raise ValueError(f"candidate boundary {label} changed")


def _accumulate_targets(
    initial: TargetUpdate, updates: tuple[TargetUpdate, ...]
) -> TargetUpdate:
    positions = {target.actuator_name: target for target in initial.targets}
    for update in updates:
        positions.update({target.actuator_name: target for target in update.targets})
    return TargetUpdate(
        offset_s=0.0,
        targets=tuple(positions.values()),
    )


def _restore_numpy_rng(state: Mapping[str, object]) -> np.random.Generator:
    bit_generator_name = state.get("bit_generator")
    bit_generators: dict[str, type[np.random.BitGenerator]] = {
        "MT19937": np.random.MT19937,
        "PCG64": np.random.PCG64,
        "PCG64DXSM": np.random.PCG64DXSM,
        "Philox": np.random.Philox,
        "SFC64": np.random.SFC64,
    }
    if not isinstance(bit_generator_name, str):
        raise ValueError("NumPy RNG state is missing its bit-generator identity")
    bit_generator_type = bit_generators.get(bit_generator_name)
    if bit_generator_type is None:
        raise ValueError(f"unsupported NumPy bit generator: {bit_generator_name!r}")
    bit_generator = bit_generator_type()
    try:
        bit_generator.state = copy.deepcopy(dict(state))
    except (TypeError, ValueError) as error:
        raise ValueError("invalid NumPy RNG state") from error
    return np.random.Generator(bit_generator)
