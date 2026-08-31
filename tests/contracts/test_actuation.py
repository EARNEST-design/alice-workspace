"""Behavioral tests for semantic actuator command contracts."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from alice.contracts.actuation import ActuatorTarget, PoseRequest

CALIBRATION_SHA256 = "a" * 64


def make_request(**overrides: object) -> PoseRequest:
    values: dict[str, object] = {
        "schema_version": "pose-request/v1",
        "request_id": "request-001",
        "run_id": "run-001",
        "hardware_id": "alice-face-v1",
        "calibration_sha256": CALIBRATION_SHA256,
        "issued_monotonic_ns": 1_000,
        "expires_monotonic_ns": 2_000,
        "targets": ({"actuator_name": "mouth_open", "normalized_position": 0.0},),
    }
    values.update(overrides)
    return PoseRequest.model_validate(values)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_actuator_target_rejects_non_finite_positions(value: float) -> None:
    """Removing finite-number validation must make unsafe targets constructible."""

    with pytest.raises(ValidationError, match="finite number"):
        ActuatorTarget(actuator_name="mouth_open", normalized_position=value)


def test_pose_request_rejects_duplicate_semantic_names() -> None:
    """Removing identity uniqueness would allow two targets for one actuator."""

    with pytest.raises(ValidationError, match="unique actuator_name"):
        make_request(
            targets=(
                {"actuator_name": "mouth_open", "normalized_position": 0.1},
                {"actuator_name": "mouth_open", "normalized_position": -0.1},
            )
        )


def test_pose_request_rejects_non_positive_validity_window() -> None:
    """Removing expiry ordering would admit commands that are stale at issuance."""

    with pytest.raises(ValidationError, match="expire after issuance"):
        make_request(expires_monotonic_ns=1_000)


def test_pose_request_reports_expiry_at_the_deadline() -> None:
    """Changing the deadline boundary could authorize a request at its expiry."""

    request = make_request()

    assert request.is_expired(now_monotonic_ns=1_999) is False
    assert request.is_expired(now_monotonic_ns=2_000) is True

