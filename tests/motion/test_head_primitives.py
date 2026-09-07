"""Behavioral tests for semantic, controller-feasible head primitives."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.motion.controller_response import load_controller_response_config
from alice.motion.head_primitives import (
    HeadAxisSemantics,
    HeadGesture,
    HeadGestureKind,
    HeadPrimitiveGenerator,
)

ROOT = Path(__file__).parents[2]
RESPONSE_PATH = ROOT / "config" / "models" / "maestro-response-v1.yaml"
MODEL_SHA256 = hashlib.sha256(b"head-gesture-test").hexdigest()


def _state(
    *,
    neck_rotation: float = 0.0,
    head_tilt: float = 0.0,
    face_pitch: float = 0.0,
) -> TargetUpdate:
    return TargetUpdate(
        offset_s=0.0,
        targets=(
            ActuatorTarget(
                actuator_name="neck_rotation",
                normalized_position=neck_rotation,
            ),
            ActuatorTarget(
                actuator_name="head_tilt",
                normalized_position=head_tilt,
            ),
            ActuatorTarget(
                actuator_name="face_pitch",
                normalized_position=face_pitch,
            ),
        ),
    )


def _gesture(
    kind: HeadGestureKind,
    *,
    amplitude: float = 0.1,
    duration_s: float = 1.6,
    cycles: int = 1,
    asymmetry: float = 0.0,
    hold_s: float = 0.2,
    recovery_s: float = 0.8,
    recovery_targets: tuple[ActuatorTarget, ...] | None = None,
) -> HeadGesture:
    actuator_name = {
        HeadGestureKind.NOD: "face_pitch",
        HeadGestureKind.SHAKE: "neck_rotation",
        HeadGestureKind.TILT: "head_tilt",
        HeadGestureKind.LOOK_UP: "face_pitch",
        HeadGestureKind.LOOK_DOWN: "face_pitch",
        HeadGestureKind.RETURN: "face_pitch",
    }[kind]
    signed_amplitude = -amplitude if kind is HeadGestureKind.LOOK_DOWN else amplitude
    return HeadGesture(
        schema_version="head-gesture/v1",
        gesture_id=f"test-{kind.value}",
        model_id="head-gestures-v1",
        model_sha256=MODEL_SHA256,
        kind=kind,
        starts_monotonic_ns=0,
        actuator_name=actuator_name,
        amplitude=signed_amplitude,
        duration_s=duration_s,
        cycles=cycles,
        asymmetry=asymmetry,
        hold_s=hold_s,
        recovery_s=recovery_s,
        recovery_targets=recovery_targets or _state().targets,
    )


def _primitives() -> HeadPrimitiveGenerator:
    return HeadPrimitiveGenerator(
        semantics=HeadAxisSemantics(
            yaw_actuator_name="neck_rotation",
            tilt_actuator_name="head_tilt",
            pitch_actuator_name="face_pitch",
        ),
        controller_config=load_controller_response_config(RESPONSE_PATH),
        cadence_hz=10.0,
    )


@pytest.mark.parametrize(
    "kind",
    [
        HeadGestureKind.NOD,
        HeadGestureKind.SHAKE,
        HeadGestureKind.TILT,
        HeadGestureKind.LOOK_UP,
        HeadGestureKind.LOOK_DOWN,
    ],
)
def test_primitive_starts_at_state_and_returns_to_recovery_targets(
    kind: HeadGestureKind,
) -> None:
    """Dropping either endpoint would introduce a horizon-seam discontinuity."""

    state = _state()
    gesture = _gesture(kind)
    horizon = _primitives().render(gesture, state)

    assert horizon.updates[0].targets == state.targets
    assert horizon.updates[-1].targets == gesture.recovery_targets
    assert all(
        left.offset_s < right.offset_s
        for left, right in zip(horizon.updates, horizon.updates[1:])
    )


@pytest.mark.parametrize(
    ("kind", "active_name"),
    [
        (HeadGestureKind.NOD, "face_pitch"),
        (HeadGestureKind.SHAKE, "neck_rotation"),
        (HeadGestureKind.TILT, "head_tilt"),
        (HeadGestureKind.LOOK_UP, "face_pitch"),
        (HeadGestureKind.LOOK_DOWN, "face_pitch"),
    ],
)
def test_primitive_uses_semantic_axis_mapping_only(
    kind: HeadGestureKind,
    active_name: str,
) -> None:
    """Position-order or channel-number lookup could move the wrong head axis."""

    state = _state(neck_rotation=0.03, head_tilt=-0.02, face_pitch=0.01)
    horizon = _primitives().render(
        _gesture(kind, recovery_targets=state.targets),
        state,
    )
    start = {item.actuator_name: item.normalized_position for item in state.targets}
    changed = {
        name
        for name, initial in start.items()
        if any(
            next(
                item.normalized_position
                for item in update.targets
                if item.actuator_name == name
            )
            != initial
            for update in horizon.updates[:-1]
        )
    }

    assert changed == {active_name}


def test_minimum_jerk_has_gentle_edges_and_exact_peak() -> None:
    """Linear or cubic interpolation would create a harsher edge or miss the peak."""

    gesture = _gesture(
        HeadGestureKind.TILT,
        amplitude=0.2,
        duration_s=1.0,
        hold_s=0.0,
        recovery_s=1.0,
    )
    horizon = _primitives().render(gesture, _state())
    values = {
        update.offset_s: next(
            target.normalized_position
            for target in update.targets
            if target.actuator_name == "head_tilt"
        )
        for update in horizon.updates
    }

    assert values[0.1] == pytest.approx(0.2 * 0.00856)
    assert values[0.5] == pytest.approx(0.1)
    assert values[1.0] == pytest.approx(0.2)
    assert values[1.9] == pytest.approx(0.2 * (1.0 - 0.99144))


def test_asymmetry_changes_opposing_lobes_without_exceeding_amplitude() -> None:
    """Unbounded asymmetry could push an oscillatory gesture beyond its envelope."""

    horizon = _primitives().render(
        _gesture(
            HeadGestureKind.SHAKE,
            amplitude=0.2,
            duration_s=2.0,
            asymmetry=0.5,
            recovery_s=0.8,
        ),
        _state(),
    )
    values = [
        next(
            target.normalized_position
            for target in update.targets
            if target.actuator_name == "neck_rotation"
        )
        for update in horizon.updates
    ]

    assert max(values) == pytest.approx(0.2)
    assert min(values) == pytest.approx(-0.2 / 3.0)


def test_return_primitive_recovers_all_semantic_head_axes() -> None:
    """Returning only one axis could strand another head axis off attention."""

    state = _state(neck_rotation=0.12, head_tilt=-0.08, face_pitch=0.1)
    gesture = _gesture(
        HeadGestureKind.RETURN,
        amplitude=0.2,
        duration_s=1.0,
        hold_s=0.0,
        recovery_s=0.8,
    )
    horizon = _primitives().render(gesture, state)

    assert horizon.updates[0].targets == state.targets
    assert horizon.updates[-1].targets == _state().targets
    assert all(
        any(target.normalized_position != 0.0 for target in update.targets)
        for update in horizon.updates[:-1]
    )


def test_return_primitive_rejects_recovery_beyond_amplitude_envelope() -> None:
    """Ignoring return amplitude would permit an unbounded multi-axis recovery."""

    gesture = _gesture(
        HeadGestureKind.RETURN,
        amplitude=0.1,
        duration_s=1.0,
        hold_s=0.0,
        recovery_s=0.8,
    )

    with pytest.raises(ValueError, match="return amplitude"):
        _primitives().render(gesture, _state(neck_rotation=0.2))


def test_response_infeasible_transition_is_rejected() -> None:
    """A nominal minimum-jerk curve must not outrun controller response priors."""

    gesture = _gesture(
        HeadGestureKind.TILT,
        amplitude=0.8,
        duration_s=0.05,
        hold_s=0.0,
        recovery_s=0.05,
    )

    with pytest.raises(ValueError, match="controller response"):
        _primitives().render(gesture, _state())


@pytest.mark.parametrize(
    ("amplitude", "limiting_derivative"),
    [
        pytest.param(0.1, "acceleration", id="acceleration"),
        pytest.param(0.8, "velocity", id="velocity"),
    ],
)
def test_quintic_just_over_controller_derivative_limit_is_rejected(
    amplitude: float,
    limiting_derivative: str,
) -> None:
    """Bang-bang arrival time does not bound the emitted quintic derivatives."""

    parameters = load_controller_response_config(RESPONSE_PATH).actuator("head_tilt")
    velocity_time_s = (15.0 / 8.0) * amplitude / parameters.max_velocity_per_s
    acceleration_time_s = math.sqrt(
        (10.0 * math.sqrt(3.0) / 3.0) * amplitude / parameters.max_acceleration_per_s2
    )
    limiting_time_s = max(velocity_time_s, acceleration_time_s)
    duration_s = limiting_time_s * (1.0 - 1e-6)
    peak_velocity = (15.0 / 8.0) * amplitude / duration_s
    peak_acceleration = (10.0 * math.sqrt(3.0) / 3.0) * amplitude / duration_s**2

    if limiting_derivative == "velocity":
        assert parameters.max_velocity_per_s < peak_velocity
        assert peak_acceleration < parameters.max_acceleration_per_s2
    else:
        assert parameters.max_acceleration_per_s2 < peak_acceleration
        assert peak_velocity < parameters.max_velocity_per_s
    gesture = _gesture(
        HeadGestureKind.TILT,
        amplitude=amplitude,
        duration_s=duration_s,
        hold_s=0.0,
        recovery_s=2.0,
    )

    with pytest.raises(ValueError, match=f"peak {limiting_derivative}"):
        _primitives().render(gesture, _state())


def test_history_record_round_trip_preserves_head_gesture() -> None:
    """Losing typed parameters would make an accepted gesture unreplayable."""

    gesture = _gesture(HeadGestureKind.NOD)

    assert HeadGesture.from_history_record(gesture.as_history_record()) == gesture


@pytest.mark.parametrize("kind", [HeadGestureKind.TILT, HeadGestureKind.LOOK_UP])
def test_non_oscillatory_gesture_rejects_unused_cycle_or_asymmetry(
    kind: HeadGestureKind,
) -> None:
    """Silently ignored parameters would make serialized gestures misleading."""

    with pytest.raises(ValidationError, match="non-oscillatory"):
        _gesture(kind, cycles=2)
    with pytest.raises(ValidationError, match="non-oscillatory"):
        _gesture(kind, asymmetry=0.2)
