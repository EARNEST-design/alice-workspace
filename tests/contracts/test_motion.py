"""Behavioral tests for sparse motion-proposal contracts."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from alice.contracts.motion import (
    MotionProposal,
    TargetUpdate,
    TargetUpdateHorizon,
)

SHA256_A = "a" * 64
SHA256_B = "b" * 64
SHA256_C = "c" * 64


def target_update(
    offset_s: float, *, names: tuple[str, ...] = ("mouth_open",)
) -> dict[str, object]:
    return {
        "offset_s": offset_s,
        "targets": tuple(
            {"actuator_name": name, "normalized_position": 0.25}
            for name in names
        ),
    }


def horizon(*, offsets: tuple[float, ...] = (0.0, 0.2)) -> dict[str, object]:
    return {
        "schema_version": "target-update-horizon/v1",
        "updates": tuple(target_update(offset) for offset in offsets),
    }


def valid_proposal(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": "motion-proposal/v1",
        "proposal_id": "proposal-001",
        "run_id": "run-001",
        "generated_monotonic_ns": 1_000,
        "expires_monotonic_ns": 2_000,
        "seed": 41,
        "model_id": "procedural-motion-v1",
        "model_sha256": SHA256_A,
        "calibration_sha256": SHA256_B,
        "controller_settings_sha256": SHA256_C,
        "horizon": horizon(),
    }
    values.update(overrides)
    return values


def test_horizon_requires_increasing_offsets() -> None:
    """Non-increasing update times would make target ordering ambiguous."""

    with pytest.raises(ValueError, match="strictly increasing"):
        TargetUpdateHorizon.model_validate(horizon(offsets=(0.2, 0.1)))


@pytest.mark.parametrize("offset", [-0.1, math.nan, math.inf, -math.inf])
def test_target_update_rejects_invalid_offsets(offset: float) -> None:
    """Sparse update times must be finite offsets at or after horizon start."""

    with pytest.raises(ValidationError):
        TargetUpdate.model_validate(target_update(offset))


def test_target_update_requires_unique_semantic_actuator_names() -> None:
    """Two values for one semantic actuator at one instant are ambiguous."""

    with pytest.raises(ValidationError, match="unique actuator_name"):
        TargetUpdate.model_validate(
            target_update(0.0, names=("mouth_open", "mouth_open"))
        )


def test_target_update_requires_at_least_one_target() -> None:
    """An empty sparse update carries no proposed motion."""

    with pytest.raises(ValidationError, match="at least one target"):
        TargetUpdate.model_validate({"offset_s": 0.0, "targets": ()})


def test_motion_proposal_preserves_replay_and_binding_identities() -> None:
    """Dropping provenance identities would make deterministic replay ambiguous."""

    proposal = MotionProposal.model_validate(valid_proposal())

    assert proposal.seed == 41
    assert proposal.model_id == "procedural-motion-v1"
    assert proposal.model_sha256 == SHA256_A
    assert proposal.calibration_sha256 == SHA256_B
    assert proposal.controller_settings_sha256 == SHA256_C


def test_motion_proposal_requires_expiry_after_generation() -> None:
    """A proposal stale when generated must fail at the contract boundary."""

    with pytest.raises(ValidationError, match="expire after generation"):
        MotionProposal.model_validate(valid_proposal(expires_monotonic_ns=1_000))


def test_motion_proposal_is_frozen() -> None:
    """Mutating a generated proposal would invalidate its replay identity."""

    proposal = MotionProposal.model_validate(valid_proposal())

    with pytest.raises(ValidationError, match="frozen"):
        proposal.seed = 7
