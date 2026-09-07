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


def test_velocity_away_from_target_does_not_create_non_monotone_motion() -> None:
    """Retaining opposite velocity could move away before correcting course."""

    observed = _model().predict(
        _state(0.0, velocity=-0.8),
        _update(0.5),
        elapsed_s=0.02,
    )

    assert 0.0 <= observed.position <= 0.5
    assert observed.velocity >= 0.0


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
