"""Behavioral tests for semantic actuator command contracts."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from alice.contracts.actuation import (
    ActuatorStatus,
    ActuatorStatusState,
    ActuatorTarget,
    PoseRequest,
)

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


def make_status(**overrides: object) -> ActuatorStatus:
    values: dict[str, object] = {
        "schema_version": "actuator-status/v1",
        "request_id": "request-001",
        "run_id": "run-001",
        "hardware_id": "alice-face-v1",
        "calibration_sha256": CALIBRATION_SHA256,
        "reported_monotonic_ns": 1_500,
        "state": ActuatorStatusState.APPLIED,
        "applied_targets": (
            {"actuator_name": "mouth_open", "normalized_position": 0.0},
        ),
    }
    values.update(overrides)
    return ActuatorStatus.model_validate(values)


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


def test_applied_status_requires_at_least_one_applied_target() -> None:
    """An empty success status must not claim that a requested pose was applied."""

    with pytest.raises(ValidationError, match="applied status requires"):
        make_status(applied_targets=())


def test_rejected_status_rejects_applied_targets() -> None:
    """A pre-forwarding rejection cannot truthfully report applied targets."""

    with pytest.raises(ValidationError, match="cannot carry applied_targets"):
        make_status(state=ActuatorStatusState.REJECTED, fault_code="not-forwarded")


def test_fault_status_preserves_partially_applied_targets() -> None:
    """A forwarding fault must retain evidence of any known physical application."""

    status = make_status(
        state=ActuatorStatusState.FAULT,
        fault_code="partial-write",
        applied_targets=({"actuator_name": "mouth_open", "normalized_position": 0.1},),
    )

    assert status.applied_targets == (
        ActuatorTarget(actuator_name="mouth_open", normalized_position=0.1),
    )


def test_fault_status_rejects_duplicate_applied_target_names() -> None:
    """Partial-state evidence remains unambiguous by semantic actuator name."""

    with pytest.raises(ValidationError, match="unique actuator_name"):
        make_status(
            state=ActuatorStatusState.FAULT,
            fault_code="partial-write",
            applied_targets=(
                {"actuator_name": "mouth_open", "normalized_position": 0.1},
                {"actuator_name": "mouth_open", "normalized_position": 0.2},
            ),
        )


def test_applied_status_rejects_fault_code() -> None:
    """A success status carrying a fault must fail closed rather than be ambiguous."""

    with pytest.raises(ValidationError, match="cannot carry a fault_code"):
        make_status(fault_code="controller-error")


@pytest.mark.parametrize(
    "state", [ActuatorStatusState.REJECTED, ActuatorStatusState.FAULT]
)
def test_non_applied_status_requires_fault_code(state: ActuatorStatusState) -> None:
    """A failed status must identify the rejecting or faulting condition."""

    with pytest.raises(ValidationError, match="requires a fault_code"):
        make_status(state=state, applied_targets=())


@pytest.mark.parametrize(
    "state", [ActuatorStatusState.REJECTED, ActuatorStatusState.FAULT]
)
def test_non_applied_status_accepts_fault_without_targets(
    state: ActuatorStatusState,
) -> None:
    """A failed adapter result has one unambiguous fail-closed representation."""

    status = make_status(
        state=state,
        applied_targets=(),
        fault_code="adapter-failure",
    )

    assert status.state is state
    assert status.applied_targets == ()
    assert status.fault_code == "adapter-failure"
