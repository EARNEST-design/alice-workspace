"""Behavioral tests for continuous affect intent filtering."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from alice.contracts.affect import AffectIntent, AffectVectorSchema
from alice.motion.intent_filter import (
    FilteredIntent,
    IntentFilter,
    IntentFilterConfig,
    SupportStatus,
)

CONFIG_PATH = (
    Path(__file__).parents[2] / "config" / "affect" / "intent-filter-v1.yaml"
)


def _intent(**overrides: object) -> AffectIntent:
    values: dict[str, object] = {
        "schema_version": "affect-intent/v1",
        "affect_schema_id": "affect-vector/v1",
        "vector": (1.0, 0.0, 0.0),
        "intensity": 0.8,
        "source_id": "operator-ui",
        "source_confidence": 0.9,
        "captured_at": datetime(2026, 9, 7, tzinfo=UTC),
        "received_monotonic_ns": 1_500_000_000,
        "expires_monotonic_ns": 4_000_000_000,
        "transition_duration_s": None,
    }
    values.update(overrides)
    return AffectIntent.model_validate(values)


def _previous() -> FilteredIntent:
    return FilteredIntent(
        schema_version="filtered-intent/v1",
        affect_schema_id="affect-vector/v1",
        vector=(0.0, 0.0, 0.0),
        intensity=0.2,
        source_id="last-valid-source",
        source_confidence=0.7,
        accepted_monotonic_ns=1_000_000_000,
        support_status=SupportStatus.SUPPORTED,
        support_distance=0.0,
        reason="within demonstrated support",
    )


def _config(**overrides: object) -> IntentFilterConfig:
    values: dict[str, object] = {
        "schema_version": "intent-filter/v1",
        "affect_schema_id": "affect-vector/v1",
        "coordinate_scales": (2.0, 2.0, 2.0),
        "retained_training_coordinates": (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
        ),
        "supported_max_distance": 0.02,
        "interpolated_max_distance": 0.2,
        "default_transition_time_constant_s": 1.0,
        "support_set_id": "unit-test-support-v1",
        "support_provenance": "hand-authored deterministic test fixture",
    }
    values.update(overrides)
    return IntentFilterConfig.model_validate(values)


def _filter() -> IntentFilter:
    schema = AffectVectorSchema(
        schema_id="affect-vector/v1",
        dimensions=("valence", "arousal", "dominance"),
    )
    config = _config()
    return IntentFilter(schema=schema, config=config)


def test_stale_intent_preserves_last_valid_target() -> None:
    """Accepting an expired update would replace the last valid target."""

    previous = _previous()
    stale = _intent(
        vector=(0.5, 0.0, 0.0),
        received_monotonic_ns=1_100_000_000,
        expires_monotonic_ns=2_000_000_000,
    )

    filtered = _filter().update(stale, previous, now_ns=2_000_000_000)

    assert filtered.support_status is SupportStatus.STALE
    assert filtered.vector == previous.vector
    assert filtered.intensity == previous.intensity
    assert filtered.accepted_monotonic_ns == previous.accepted_monotonic_ns
    assert filtered.support_distance == pytest.approx(0.25 / math.sqrt(3.0))
    assert "expired" in filtered.reason


def test_future_received_intent_is_stale_and_preserves_full_prior_state() -> None:
    """Accepting a not-yet-received update would violate monotonic ordering."""

    previous = _previous()
    future = _intent(
        vector=(0.5, 0.0, 0.0),
        received_monotonic_ns=2_500_000_000,
        expires_monotonic_ns=4_000_000_000,
    )

    filtered = _filter().update(future, previous, now_ns=2_000_000_000)

    retained_fields = {
        "schema_version",
        "affect_schema_id",
        "vector",
        "intensity",
        "source_id",
        "source_confidence",
        "accepted_monotonic_ns",
    }
    assert filtered.support_status is SupportStatus.STALE
    assert filtered.model_dump(include=retained_fields) == previous.model_dump(
        include=retained_fields
    )
    assert filtered.support_distance == pytest.approx(0.25 / math.sqrt(3.0))
    assert "precedes intent receipt" in filtered.reason


@pytest.mark.parametrize(
    "overrides",
    [
        {"affect_schema_id": "affect-vector/v2"},
        {"vector": (0.1, 0.2)},
    ],
)
def test_schema_incompatible_intent_has_undefined_support_distance(
    overrides: dict[str, object],
) -> None:
    """Skipping schema validation could reinterpret coordinate dimensions."""

    previous = _previous()
    incompatible = _intent(**overrides)

    filtered = _filter().update(incompatible, previous, now_ns=2_000_000_000)

    assert filtered.support_status is SupportStatus.FALLBACK
    assert filtered.vector == previous.vector
    assert filtered.intensity == previous.intensity
    assert filtered.support_distance is None
    assert "schema" in filtered.reason


def test_first_order_filter_uses_monotonic_elapsed_time() -> None:
    """Using wall time or a fixed step would produce the wrong transition state."""

    filtered = _filter().update(_intent(), _previous(), now_ns=2_000_000_000)

    alpha = 1.0 - math.exp(-1.0)
    assert filtered.vector == pytest.approx((alpha, 0.0, 0.0))
    assert filtered.intensity == pytest.approx(0.2 + alpha * 0.6)
    assert filtered.accepted_monotonic_ns == 2_000_000_000
    assert filtered.support_status is SupportStatus.SUPPORTED


def test_intent_transition_preference_overrides_default_time_constant() -> None:
    """Ignoring a valid transition preference would apply the wrong smoothing rate."""

    intent = _intent(transition_duration_s=2.0)

    filtered = _filter().update(intent, _previous(), now_ns=2_000_000_000)

    alpha = 1.0 - math.exp(-0.5)
    assert filtered.vector == pytest.approx((alpha, 0.0, 0.0))


def test_support_distance_uses_normalized_continuous_coordinates() -> None:
    """Using an unnormalized or categorical lookup would misclassify interpolation."""

    intent = _intent(vector=(0.5, 0.0, 0.0), cluster_labels=("ignored-label",))

    filtered = _filter().update(intent, _previous(), now_ns=2_000_000_000)

    assert filtered.support_status is SupportStatus.INTERPOLATED
    assert filtered.support_distance == pytest.approx(0.25 / math.sqrt(3.0))
    assert "interpolation" in filtered.reason


def test_outside_demonstrated_support_requests_fallback() -> None:
    """Treating an outlier as supported would permit unconstrained extrapolation."""

    previous = _previous()
    outlier = _intent(vector=(1.0, 1.0, 1.0))

    filtered = _filter().update(outlier, previous, now_ns=2_000_000_000)

    assert filtered.support_status is SupportStatus.FALLBACK
    assert filtered.support_distance == pytest.approx(math.sqrt(1.0 / 6.0))
    assert filtered.vector == previous.vector
    assert filtered.intensity == previous.intensity
    assert "outside" in filtered.reason


def test_cluster_labels_do_not_change_filtering_or_support() -> None:
    """Reading descriptive labels would turn continuous intent into categories."""

    unlabeled = _filter().update(_intent(), _previous(), now_ns=2_000_000_000)
    labeled = _filter().update(
        _intent(cluster_labels=("joy", "high-energy")),
        _previous(),
        now_ns=2_000_000_000,
    )

    assert labeled.vector == unlabeled.vector
    assert labeled.support_status is unlabeled.support_status
    assert labeled.support_distance == unlabeled.support_distance


def test_checked_in_filter_configuration_is_valid_and_provenanced() -> None:
    """Placeholder neutral data must not masquerade as demonstrated support."""

    document = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

    config = IntentFilterConfig.model_validate(document)
    filtered = IntentFilter(
        schema=AffectVectorSchema(
            schema_id="affect-vector/v1",
            dimensions=("valence", "arousal", "dominance"),
        ),
        config=config,
    ).update(
        _intent(vector=(0.0, 0.0, 0.0)),
        _previous(),
        now_ns=2_000_000_000,
    )

    assert config.affect_schema_id == "affect-vector/v1"
    assert config.retained_training_coordinates == ()
    assert config.support_provenance
    assert filtered.support_status is SupportStatus.FALLBACK
    assert filtered.support_distance is None
    assert "no retained support evidence" in filtered.reason


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("coordinate_scales", (math.inf, 2.0, 2.0)),
        ("default_transition_time_constant_s", math.inf),
    ],
)
def test_filter_configuration_rejects_non_finite_geometry(
    field: str,
    value: object,
) -> None:
    """Accepting infinity would collapse support distance or filter progress."""

    with pytest.raises(ValidationError):
        _config(**{field: value})
