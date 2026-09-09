"""Synthetic production-composer qualification; no fitted or physical claims.

Regressions caught: static/suppressed facial events, lost accepted phase, incomplete
targets, and divergent restored continuation. Task 3's test_streaming.py covers
stale-mid-blink recovery and rejection after speculative lookahead separately.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
import torch
from safetensors.torch import save

from alice.contracts.motion import TargetUpdate
from alice.models.head_scheduler import HeadGestureScheduler, load_head_gesture_config
from alice.models.residual_state_space import (
    ResidualStateSpace,
    load_residual_state_space_config,
)
from alice.motion.anchors import AnchorPlanner, load_procedural_motion_config
from alice.motion.controller_response import (
    ControllerResponse,
    load_controller_response_config,
)
from alice.motion.face_events import (
    FaceEvent,
    FaceEventGenerator,
    load_face_event_config,
)
from alice.motion.head_primitives import HeadGesture
from alice.motion.intent_filter import FilteredIntent, SupportStatus
from alice.motion.state import ActuatorVelocity, GeneratorState, dump_state, load_state
from alice.motion.streaming import (
    AcceptedPrefix,
    CandidatePlan,
    ProductionCandidateComposer,
    StreamingMotionGenerator,
)

CONFIG = Path(__file__).parents[2] / "config" / "models"
MODEL_SEED = 29
PREFIX_S = 0.4
HORIZON_S = 1.0


def _intent(at_ns: int) -> FilteredIntent:
    return FilteredIntent(
        schema_version="filtered-intent/v1",
        affect_schema_id="affect-vector/v1",
        vector=(0.0, 0.0, 0.0),
        intensity=0.7,
        source_id="synthetic-composed-qualification",
        accepted_monotonic_ns=at_ns,
        support_status=SupportStatus.SUPPORTED,
        support_distance=0.0,
        reason="configured priors and explicitly zero residual; no fitted claim",
    )


class _ObservedComposer(ProductionCandidateComposer):
    """Observe the public proposal status without changing candidate generation."""

    last_status: str = ""

    def plan(
        self,
        intent: FilteredIntent,
        state: GeneratorState,
        horizon_s: float,
        prefix_duration_s: float,
        *,
        generated_monotonic_ns: int,
    ) -> CandidatePlan:
        result = super().plan(
            intent,
            state,
            horizon_s,
            prefix_duration_s,
            generated_monotonic_ns=generated_monotonic_ns,
        )
        self.last_status = result.proposal.support_status
        return result


@dataclass
class _Baseline:
    runtime: StreamingMotionGenerator
    composer: _ObservedComposer
    initial: GeneratorState
    config_hashes: dict[str, str]
    channels: dict[str, tuple[str, ...]]


def _baseline(seed: int) -> _Baseline:
    anchor = load_procedural_motion_config(CONFIG / "procedural-motion-v1.yaml")
    response = load_controller_response_config(CONFIG / "maestro-response-v1.yaml")
    face = load_face_event_config(CONFIG / "face-events-v1.yaml")
    head = load_head_gesture_config(CONFIG / "head-gestures-v1.yaml")
    residual_config = load_residual_state_space_config(
        CONFIG / "residual-state-space-v1.yaml"
    )
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(MODEL_SEED)
        residual = ResidualStateSpace(residual_config)
    with torch.no_grad():
        for parameter in residual.parameters():
            parameter.zero_()
    hashes = {
        name: hashlib.sha256(config.model_dump_json().encode()).hexdigest()
        for name, config in (
            ("anchor", anchor),
            ("response", response),
            ("face", face),
            ("head", head),
            ("residual", residual_config),
        )
    }
    hashes["weights"] = hashlib.sha256(save(residual.state_dict())).hexdigest()
    composer = _ObservedComposer(
        anchor_planner=AnchorPlanner(config=anchor),
        residual_model=residual,
        face_events=FaceEventGenerator(config=face, controller_config=response),
        head_scheduler=HeadGestureScheduler(config=head, controller_config=response),
        controller_response=ControllerResponse(config=response),
    )
    target = TargetUpdate(offset_s=0.0, targets=anchor.anchor("neutral").targets)
    initial = GeneratorState(
        schema_version="generator-state/v1",
        last_accepted_target=target,
        last_reported_pose=target,
        estimated_velocity=tuple(
            ActuatorVelocity(actuator_name=t.actuator_name, velocity_per_s=0.0)
            for t in target.targets
        ),
        filtered_intent=_intent(0),
        latent_vector=(0.0,) * residual_config.hidden_size,
        numpy_rng_state=np.random.default_rng(seed).bit_generator.state,
        torch_rng_state=tuple(
            int(v) for v in torch.Generator().manual_seed(seed).get_state()
        ),
        event_history=(),
        model_id="synthetic-zero-residual-v1",
        model_sha256=hashlib.sha256(
            json.dumps(hashes, sort_keys=True).encode()
        ).hexdigest(),
        calibration_sha256=anchor.calibration_sha256,
        controller_settings_sha256=anchor.controller_settings_sha256,
        monotonic_ns=0,
    )
    head_names = head.semantics.actuator_names
    event_names = [name for policy in face.events for name in policy.actuator_names]
    assert len(event_names) == len(set(event_names))
    assert not set(event_names) & set(head_names)
    return _Baseline(
        StreamingMotionGenerator(
            generator=composer, horizon_s=HORIZON_S, prefix_duration_s=PREFIX_S
        ),
        composer,
        initial,
        hashes,
        {
            "head": head_names,
            "face": tuple(
                name
                for name in anchor.semantic_actuator_names
                if name not in head_names
            ),
        },
    )


def _positions(update: TargetUpdate) -> dict[str, float]:
    return {t.actuator_name: t.normalized_position for t in update.targets}


def _check_prefix(
    prefix: AcceptedPrefix, before: GeneratorState, after: GeneratorState
) -> None:
    names = set(_positions(before.last_accepted_target))
    assert prefix.starts_at_ns == before.monotonic_ns
    assert prefix.ends_at_ns == after.monotonic_ns
    assert _positions(prefix.updates[0]) == _positions(before.last_accepted_target)
    assert _positions(prefix.updates[-1]) == _positions(after.last_accepted_target)
    for update in prefix.updates:
        positions = _positions(update)
        assert set(positions) == names
        assert all(np.isfinite(v) and -1.0 <= v <= 1.0 for v in positions.values())
    for record in before.event_history:
        if record.event_type in {"face-event/v1", "head-gesture/v1"} and (
            record.started_monotonic_ns <= before.monotonic_ns
            and record.ended_monotonic_ns > after.monotonic_ns
        ):
            # Equality includes absolute onset/end and envelope/primitive parameters.
            assert record in after.event_history


def _metrics(
    prefixes: list[AcceptedPrefix], channels: dict[str, tuple[str, ...]]
) -> dict[str, object]:
    """One-sided finite differences of requested commands, not measured motion."""
    result: dict[str, object] = {
        "formulas": {
            "position_jump": "max_abs(next[0] - prev[-1])",
            "velocity_change": "max_abs((next[1]-next[0])/h - (prev[-1]-prev[-2])/h)",
            "acceleration_change": (
                "max_abs((next[2]-2*next[1]+next[0])/h^2"
                " - (prev[-1]-2*prev[-2]+prev[-3])/h^2)"
            ),
            "units": "normalized command position; position/s; position/s^2",
            "interpretation": "adjacent one-sided estimates; not physical derivatives",
        },
    }
    sample_steps = [np.diff([u.offset_s for u in p.updates]) for p in prefixes]
    h = float(sample_steps[0][0])
    assert all(np.allclose(steps, h, rtol=0.0, atol=1e-12) for steps in sample_steps)
    result["command_sample_interval_s"] = h
    for group, names in channels.items():
        arrays = [
            np.array(
                [
                    [_positions(update)[name] for name in names]
                    for update in prefix.updates
                ]
            )
            for prefix in prefixes
        ]
        maxima = np.zeros(3)
        for previous, following in zip(arrays, arrays[1:]):
            changes = (
                following[0] - previous[-1],
                (following[1] - following[0] - previous[-1] + previous[-2]) / h,
                (
                    following[2]
                    - 2 * following[1]
                    + following[0]
                    - previous[-1]
                    + 2 * previous[-2]
                    - previous[-3]
                )
                / h**2,
            )
            maxima = np.maximum(maxima, [np.max(np.abs(c)) for c in changes])
        samples = np.concatenate(arrays)
        assert np.all(np.isfinite(maxima))
        assert maxima[0] == 0.0
        result[group] = {
            "max_boundary_position_jump": float(maxima[0]),
            "max_boundary_velocity_change_per_s": float(maxima[1]),
            "max_boundary_acceleration_change_per_s2": float(maxima[2]),
            "channel_ranges": {
                name: [float(samples[:, i].min()), float(samples[:, i].max())]
                for i, name in enumerate(names)
            },
        }
    return result


@pytest.mark.parametrize("seed", [7, 29])
def test_steady_composed_baseline_has_accepted_events_and_exact_restart(
    seed: int, record_testsuite_property: Callable[[str, object], None]
) -> None:
    baseline, replay = _baseline(seed), _baseline(seed)
    state, restored = baseline.initial, load_state(dump_state(replay.initial))
    prefixes: list[AcceptedPrefix] = []
    statuses: Counter[str] = Counter()
    motion_events: dict[str, str] = {}
    head_events: set[str] = set()
    digest = hashlib.sha256()
    for _ in range(150):
        intent = _intent(state.monotonic_ns)
        original = dump_state(state)
        prefix, boundary = baseline.runtime.replan(intent, state, state.monotonic_ns)
        replay_prefix, replay_boundary = replay.runtime.replan(
            intent, restored, restored.monotonic_ns
        )
        assert (replay_prefix, replay_boundary) == (prefix, boundary)
        assert dump_state(state) == original
        _check_prefix(prefix, state, boundary)
        statuses[baseline.composer.last_status] += 1
        for record in boundary.event_history:
            if record.event_type == "head-gesture/v1":
                if record.started_monotonic_ns < boundary.monotonic_ns:
                    head_events.add(HeadGesture.from_history_record(record).gesture_id)
            if record.event_type != "face-event/v1":
                continue
            event = FaceEvent.from_history_record(record)
            for previous, update in zip(prefix.updates, prefix.updates[1:]):
                at_ns = prefix.starts_at_ns + round(update.offset_s * 1e9)
                if event.starts_monotonic_ns < at_ns < event.ends_monotonic_ns:
                    # With neutral eye anchors and zero residual, changed nonzero
                    # event-owned channels establish accepted event motion.
                    if all(
                        abs(_positions(update)[name]) > 1e-9
                        and abs(_positions(update)[name] - _positions(previous)[name])
                        > 1e-9
                        for name in event.actuator_names
                    ):
                        motion_events[event.event_id] = event.kind.value
        prefixes.append(prefix)
        digest.update(prefix.model_dump_json().encode())
        state, restored = boundary, load_state(dump_state(replay_boundary))
        assert restored == state
    counts = Counter(motion_events.values())
    assert counts["blink"] > 0 and counts["gaze"] > 0
    assert statuses["supported"] > 0
    assert state.monotonic_ns == 60_000_000_000
    report = {
        "baseline": "synthetic zero-weight residual with configured unfitted priors",
        "runtime_seed": seed,
        "model_seed": MODEL_SEED,
        "prefix_s": PREFIX_S,
        "horizon_s": HORIZON_S,
        "duration_s": 60.0,
        "config_hashes": baseline.config_hashes,
        "prefixes": len(prefixes),
        "proposal_status_counts": dict(statuses),
        "accepted_face_events_with_motion": dict(counts),
        "accepted_head_event_count": len(head_events),
        "same_revision_restart_equal": True,
        "prefix_sha256": digest.hexdigest(),
        "metrics": _metrics(prefixes, baseline.channels),
    }
    record_testsuite_property(
        f"composed_baseline_seed_{seed}", json.dumps(report, sort_keys=True)
    )


def test_supported_intent_transition_retains_accepted_head_gesture(
    record_testsuite_property: Callable[[str, object], None],
) -> None:
    baseline = _baseline(7)
    state = baseline.initial
    active = None
    for _ in range(150):
        previous_prefix, state = baseline.runtime.replan(
            _intent(state.monotonic_ns), state, state.monotonic_ns
        )
        active = next(
            (
                HeadGesture.from_history_record(r)
                for r in state.event_history
                if r.event_type == "head-gesture/v1"
                and r.started_monotonic_ns < state.monotonic_ns
                and r.ended_monotonic_ns > state.monotonic_ns + 400_000_000
            ),
            None,
        )
        if active is not None:
            break
    assert active is not None, "fixture must transition during an accepted gesture"
    assert (
        abs(
            _positions(state.last_accepted_target)[active.actuator_name]
            - next(
                t.normalized_position
                for t in active.initial_targets
                if t.actuator_name == active.actuator_name
            )
        )
        > 1e-3
    )
    transition_ns = state.monotonic_ns
    changed = _intent(transition_ns).model_copy(
        update={
            "vector": (0.4, -0.3, 0.2),
            "intensity": 0.2,
        }
    )
    # Include the incoming prefix so metrics measure the intent-change boundary.
    prefixes: list[AcceptedPrefix] = [previous_prefix]
    restored = load_state(dump_state(state))
    statuses: Counter[str] = Counter()
    while state.monotonic_ns < active.ends_monotonic_ns:
        intent = changed.model_copy(
            update={"accepted_monotonic_ns": state.monotonic_ns}
        )
        prefix, boundary = baseline.runtime.replan(intent, state, state.monotonic_ns)
        assert baseline.runtime.replan(intent, restored, restored.monotonic_ns) == (
            prefix,
            boundary,
        )
        _check_prefix(prefix, state, boundary)
        assert active.as_history_record() in boundary.event_history
        assert boundary.filtered_intent == intent
        prefixes.append(prefix)
        statuses[baseline.composer.last_status] += 1
        state, restored = boundary, load_state(dump_state(boundary))
    values = [_positions(u)[active.actuator_name] for p in prefixes for u in p.updates]
    assert max(values) - min(values) > 0.01
    assert _positions(state.last_accepted_target)[
        active.actuator_name
    ] == pytest.approx(
        next(
            t.normalized_position
            for t in active.recovery_targets
            if t.actuator_name == active.actuator_name
        ),
        abs=1e-8,
    )
    record_testsuite_property(
        "composed_supported_transition",
        json.dumps(
            {
                "runtime_seed": 7,
                "model_seed": MODEL_SEED,
                "config_hashes": baseline.config_hashes,
                "transition_ns": transition_ns,
                "gesture": active.model_dump(mode="json"),
                "before_vector": [0.0, 0.0, 0.0],
                "after_vector": list(changed.vector),
                "before_intensity": 0.7,
                "after_intensity": changed.intensity,
                "retained_absolute_phase": True,
                "same_revision_restart_equal": True,
                "proposal_status_counts": dict(statuses),
                "metrics": _metrics(prefixes, baseline.channels),
            },
            sort_keys=True,
        ),
    )
