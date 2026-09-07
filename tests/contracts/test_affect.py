"""Behavioral tests for continuous affect-intent contracts."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from alice.contracts.affect import AffectIntent, AffectVectorSchema

SCHEMA_PATH = Path(__file__).parents[2] / "config" / "affect" / "affect-vector-v1.yaml"


def valid_intent(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": "affect-intent/v1",
        "affect_schema_id": "affect-vector/v1",
        "vector": (0.2, -0.3, 0.8),
        "intensity": 0.6,
        "source_id": "operator-ui",
        "source_confidence": None,
        "captured_at": datetime(2026, 9, 7, tzinfo=UTC),
        "received_monotonic_ns": 1_000,
        "expires_monotonic_ns": 2_000,
        "transition_duration_s": None,
    }
    values.update(overrides)
    return values


def test_affect_is_continuous_and_labels_are_optional() -> None:
    """Making a cluster label mandatory would turn metadata into the interface."""

    intent = AffectIntent.model_validate(valid_intent(vector=(0.2, -0.3, 0.8)))

    assert intent.vector == (0.2, -0.3, 0.8)
    assert intent.cluster_labels == ()


@pytest.mark.parametrize("value", [-1.0001, 1.0001, math.nan, math.inf, -math.inf])
def test_affect_rejects_out_of_range_or_non_finite_coordinates(value: float) -> None:
    """Weak coordinate validation would admit an undefined affect request."""

    with pytest.raises(ValidationError):
        AffectIntent.model_validate(valid_intent(vector=(value, 0.0, 0.0)))


@pytest.mark.parametrize("value", [-0.0001, 1.0001, math.nan, math.inf, -math.inf])
def test_affect_rejects_invalid_intensity(value: float) -> None:
    """Intensity must remain a finite normalized scalar."""

    with pytest.raises(ValidationError):
        AffectIntent.model_validate(valid_intent(intensity=value))


def test_affect_requires_expiry_after_receipt() -> None:
    """Removing expiry ordering would admit an intent stale on arrival."""

    with pytest.raises(ValidationError, match="expire after receipt"):
        AffectIntent.model_validate(valid_intent(expires_monotonic_ns=1_000))


def test_affect_reports_expiry_at_the_deadline() -> None:
    """Changing the boundary could keep an intent authoritative past its TTL."""

    intent = AffectIntent.model_validate(valid_intent())

    assert intent.is_expired(now_monotonic_ns=1_999) is False
    assert intent.is_expired(now_monotonic_ns=2_000) is True


def test_affect_vector_schema_validates_ordered_dimension_count() -> None:
    """A mismatched vector width must not silently change dimension semantics."""

    schema = AffectVectorSchema(
        schema_id="affect-vector/v1",
        dimensions=("valence", "arousal", "dominance"),
    )

    with pytest.raises(ValueError, match="dimension count"):
        schema.validate_intent(
            AffectIntent.model_validate(valid_intent(vector=(0.1, 0.2)))
        )


def test_versioned_affect_schema_config_is_valid() -> None:
    """A malformed checked-in dimension order would make vectors ambiguous."""

    document = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema = AffectVectorSchema.model_validate(document)

    assert schema.schema_id == "affect-vector/v1"
    assert schema.dimensions == ("valence", "arousal", "dominance")


def test_affect_contract_is_frozen() -> None:
    """Mutating an accepted intent would break deterministic replay evidence."""

    intent = AffectIntent.model_validate(valid_intent())

    with pytest.raises(ValidationError, match="frozen"):
        intent.intensity = 0.1
