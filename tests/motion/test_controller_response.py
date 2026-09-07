"""Behavioral tests for the hardware-independent controller-response model."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.motion.controller_response import (
    ControllerLimitMode,
    ControllerResponse,
    ControllerResponseConfig,
    ControllerState,
    load_controller_response_config,
)

ROOT = Path(__file__).parents[2]
CONFIG_PATH = ROOT / "config" / "models" / "maestro-response-v1.yaml"
MANIFEST_PATH = ROOT / "hardware" / "alice-face-v1.yaml"
CALIBRATION_SHA256 = "8ad9ad1f59dc70c47515c9a37b4531c5070c22a40ed66fb673d0e72a0d3dadb4"


def _update(
    target: float,
    *,
    actuator_name: str = "neck_rotation",
    offset_s: float = 0.0,
) -> TargetUpdate:
    return TargetUpdate(
        offset_s=offset_s,
        targets=(
            ActuatorTarget(
                actuator_name=actuator_name,
                normalized_position=target,
            ),
        ),
    )


def _state(
    position: float,
    *,
    actuator_name: str = "neck_rotation",
    velocity: float = 0.0,
    calibration_sha256: str = CALIBRATION_SHA256,
) -> ControllerState:
    return ControllerState(
        schema_version="controller-state/v1",
        actuator_name=actuator_name,
        calibration_sha256=calibration_sha256,
        position=position,
        velocity=velocity,
    )


def _model() -> ControllerResponse:
    return ControllerResponse(config=load_controller_response_config(CONFIG_PATH))


def test_response_never_overshoots_target() -> None:
    """Failing to clamp a large integration step could cross the target."""

    observed = _model().predict(
        _state(0.0, velocity=0.7),
        _update(0.8),
        elapsed_s=5.0,
    )

    assert 0.0 <= observed.position <= 0.8
    assert observed.position == 0.8
    assert observed.velocity == 0.0


def test_response_is_monotone_toward_a_lower_target() -> None:
    """Applying unsigned motion could move a descending state away from target."""

    observed = _model().predict(
        _state(0.6, velocity=0.0),
        _update(-0.4),
        elapsed_s=0.02,
    )

    assert -0.4 <= observed.position < 0.6
    assert observed.velocity < 0.0


def test_response_limits_acceleration_and_speed() -> None:
    """Skipping either configured limit would make simulated motion unrealistic."""

    model = _model()
    parameters = model.config.actuator("neck_rotation")
    first = model.predict(_state(0.0), _update(1.0), elapsed_s=0.01)
    later = model.predict(first, _update(1.0), elapsed_s=2.0)

    assert first.velocity == pytest.approx(
        parameters.max_acceleration_per_s2 * 0.01
    )
    assert abs(later.velocity) <= parameters.max_velocity_per_s
    assert first.position > 0.0


def test_target_reversal_brakes_without_instantaneous_velocity_change() -> None:
    """Discarding away-directed velocity would violate the acceleration limit."""

    observed = _model().predict(
        _state(0.0, velocity=-0.8),
        _update(0.5),
        elapsed_s=0.02,
    )

    assert observed.position == pytest.approx(-0.0156)
    assert observed.velocity == pytest.approx(-0.76)


@pytest.mark.parametrize(
    ("position", "velocity", "target"),
    [
        pytest.param(0.99, 0.8, 0.5, id="positive-endpoint"),
        pytest.param(-0.99, -0.8, -0.5, id="negative-endpoint"),
    ],
)
def test_reversal_rejects_turnaround_beyond_normalized_endpoint(
    position: float,
    velocity: float,
    target: float,
) -> None:
    """Braking an away-directed velocity must not emit out-of-range state."""

    with pytest.raises(ValueError, match="normalized endpoint"):
        _model().predict(
            _state(position, velocity=velocity),
            _update(target),
            elapsed_s=0.1,
        )


def test_response_cannot_arrive_before_accelerate_cruise_brake_minimum() -> None:
    """Cruising through the braking interval would fabricate early arrival."""

    model = _model()

    before_minimum = model.predict(_state(0.0), _update(1.0), elapsed_s=1.64)
    at_minimum = model.predict(_state(0.0), _update(1.0), elapsed_s=1.65)

    assert before_minimum.position < 1.0
    assert before_minimum.velocity == pytest.approx(0.02)
    assert at_minimum.position == pytest.approx(1.0)
    assert at_minimum.velocity == pytest.approx(0.0)


def test_arrival_step_respects_configured_acceleration() -> None:
    """Clamping arrival velocity to zero could exceed allowed deceleration."""

    model = _model()
    state = _state(0.84, velocity=0.8)
    elapsed_s = 0.2

    observed = model.predict(state, _update(1.0), elapsed_s=elapsed_s)

    allowed_velocity_delta = (
        model.config.actuator("neck_rotation").max_acceleration_per_s2
        * elapsed_s
    )
    assert abs(observed.velocity - state.velocity) <= allowed_velocity_delta
    assert observed.position == pytest.approx(0.96)
    assert observed.velocity == pytest.approx(0.4)


def test_response_rejects_state_that_cannot_stop_before_target() -> None:
    """No predictor can preserve both limits and target bounds from this state."""

    with pytest.raises(ValueError, match="stopping distance"):
        _model().predict(
            _state(0.95, velocity=0.8),
            _update(1.0),
            elapsed_s=0.02,
        )


def test_response_rejects_state_above_configured_speed() -> None:
    """Clamping an over-speed input would hide an invalid controller state."""

    model = _model()
    over_limit = math.nextafter(
        model.config.actuator("neck_rotation").max_velocity_per_s,
        math.inf,
    )

    with pytest.raises(ValueError, match="velocity exceeds"):
        model.predict(
            _state(0.0, velocity=over_limit),
            _update(1.0),
            elapsed_s=0.02,
        )


def test_repeated_small_steps_cross_braking_boundary_without_false_rejection() -> None:
    """Floating-point noise at the braking switch must not reject valid rollout."""

    model = _model()
    state = _state(0.0)
    for _ in range(83):
        state = model.predict(state, _update(1.0), elapsed_s=0.02)

    assert state.position == pytest.approx(1.0)
    assert state.velocity == pytest.approx(0.0)


def test_zero_elapsed_time_preserves_the_complete_state() -> None:
    """Reconciliation without elapsed time would create an instantaneous jump."""

    state = _state(0.0, velocity=-0.8)

    observed = _model().predict(state, _update(0.5), elapsed_s=0.0)

    assert observed == state


def test_zero_maestro_settings_resolve_to_named_positive_response_limits() -> None:
    """Multiplying by setting zero would incorrectly model a stationary actuator."""

    model = _model()
    parameters = model.config.actuator("lower_eyelids")

    observed = model.predict(
        _state(0.0, actuator_name="lower_eyelids"),
        _update(0.5, actuator_name="lower_eyelids"),
        elapsed_s=0.02,
    )

    assert parameters.firmware_speed_setting == 0
    assert parameters.firmware_acceleration_setting == 0
    assert parameters.speed_mode is ControllerLimitMode.ZERO_SETTING_RESPONSE_ESTIMATE
    assert (
        parameters.acceleration_mode
        is ControllerLimitMode.ZERO_SETTING_RESPONSE_ESTIMATE
    )
    assert parameters.max_velocity_per_s > 0.0
    assert parameters.max_acceleration_per_s2 > 0.0
    assert observed.position > 0.0


def test_checked_in_config_uses_exact_manifest_semantic_actuator_identities() -> None:
    """Channel-number or stale names could silently select the wrong response."""

    config = load_controller_response_config(CONFIG_PATH)
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert config.hardware_id == manifest["hardware_id"]
    assert {item.actuator_name for item in config.actuators} == {
        item["name"] for item in manifest["actuators"]
    }
    assert {
        item.actuator_name: (
            item.firmware_speed_setting,
            item.firmware_acceleration_setting,
        )
        for item in config.actuators
    } == {
        item["name"]: (
            item["firmware_speed"],
            item["firmware_acceleration"],
        )
        for item in manifest["actuators"]
    }


def test_response_rejects_mismatched_or_missing_semantic_target() -> None:
    """Using another actuator's sparse target would break semantic isolation."""

    with pytest.raises(ValueError, match="does not contain"):
        _model().predict(
            _state(0.0),
            _update(0.5, actuator_name="head_tilt"),
            elapsed_s=0.02,
        )


def test_response_rejects_calibration_identity_mismatch() -> None:
    """Applying parameters to another calibration would invalidate the estimate."""

    with pytest.raises(ValueError, match="calibration"):
        _model().predict(
            _state(0.0, calibration_sha256="a" * 64),
            _update(0.5),
            elapsed_s=0.02,
        )


@pytest.mark.parametrize("elapsed_s", [-0.01, math.inf, math.nan])
def test_response_rejects_invalid_elapsed_time(elapsed_s: float) -> None:
    """Negative or non-finite time would invalidate response integration."""

    with pytest.raises(ValueError, match="elapsed_s"):
        _model().predict(_state(0.0), _update(0.5), elapsed_s=elapsed_s)


def test_config_rejects_numeric_zero_limit_interpretation() -> None:
    """A zero firmware setting must never resolve as zero physical motion."""

    document = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    zero_setting = next(
        item for item in document["actuators"] if item["firmware_speed_setting"] == 0
    )
    zero_setting["speed_mode"] = "controller-limited"

    with pytest.raises(ValidationError, match="setting 0"):
        ControllerResponseConfig.model_validate(document)
