"""Hardware-independent filtering and support qualification for affect intent."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.affect import (
    AffectCoordinate,
    AffectIntent,
    AffectVectorSchema,
    MonotonicNanoseconds,
    UnitIntervalFloat,
)
from alice.contracts.blendshapes import NonEmptyString

FinitePositiveFloat = Annotated[
    float,
    Field(gt=0.0, allow_inf_nan=False),
]


class SupportStatus(StrEnum):
    """Relationship between a requested coordinate and demonstrated support."""

    SUPPORTED = "supported"
    INTERPOLATED = "interpolated"
    FALLBACK = "fallback"
    STALE = "stale"


class IntentFilterConfig(BaseModel):
    """Versioned parameters and auditable support evidence for intent filtering."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["intent-filter/v1"]
    affect_schema_id: NonEmptyString
    coordinate_scales: tuple[FinitePositiveFloat, ...] = Field(min_length=1)
    retained_training_coordinates: tuple[tuple[AffectCoordinate, ...], ...]
    supported_max_distance: float = Field(ge=0.0, allow_inf_nan=False)
    interpolated_max_distance: float = Field(ge=0.0, allow_inf_nan=False)
    default_transition_time_constant_s: FinitePositiveFloat
    support_set_id: NonEmptyString
    support_provenance: NonEmptyString

    @model_validator(mode="after")
    def validate_support_geometry(self) -> IntentFilterConfig:
        if self.supported_max_distance > self.interpolated_max_distance:
            raise ValueError(
                "supported distance must not exceed interpolated distance"
            )
        width = len(self.coordinate_scales)
        if any(
            len(coordinate) != width
            for coordinate in self.retained_training_coordinates
        ):
            raise ValueError(
                "retained training coordinate width must match coordinate scales"
            )
        return self


class FilteredIntent(BaseModel):
    """Last accepted continuous target plus support qualification metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["filtered-intent/v1"]
    affect_schema_id: NonEmptyString
    vector: tuple[AffectCoordinate, ...] = Field(min_length=1)
    intensity: UnitIntervalFloat
    source_id: NonEmptyString
    source_confidence: UnitIntervalFloat | None = None
    accepted_monotonic_ns: MonotonicNanoseconds
    support_status: SupportStatus
    support_distance: float | None = Field(
        default=None,
        ge=0.0,
        allow_inf_nan=False,
        description=(
            "Normalized distance from the requested vector to retained support; "
            "undefined only for incompatible vector geometry or an empty evidence set"
        ),
    )
    reason: NonEmptyString


class IntentFilter:
    """Qualify and smooth continuous affect intent without hardware access."""

    def __init__(
        self,
        *,
        schema: AffectVectorSchema,
        config: IntentFilterConfig,
    ) -> None:
        if config.affect_schema_id != schema.schema_id:
            raise ValueError(
                "intent filter config does not match affect schema identity"
            )
        if len(config.coordinate_scales) != len(schema.dimensions):
            raise ValueError(
                "intent filter coordinate scale count does not match affect schema"
            )
        self._schema = schema
        self._config = config

    def update(
        self,
        intent: AffectIntent,
        previous: FilteredIntent,
        now_ns: int,
    ) -> FilteredIntent:
        """Return the qualified target at ``now_ns`` using monotonic elapsed time."""

        self._validate_previous(previous, now_ns=now_ns)
        try:
            self._schema.validate_intent(intent)
        except ValueError as error:
            return self._retained(
                previous,
                status=SupportStatus.FALLBACK,
                support_distance=None,
                reason=f"schema-incompatible affect intent: {error}",
            )

        support_distance = self._support_distance(intent.vector)
        if now_ns < intent.received_monotonic_ns:
            return self._retained(
                previous,
                status=SupportStatus.STALE,
                support_distance=support_distance,
                reason="monotonic evaluation time precedes intent receipt",
            )
        if intent.is_expired(now_monotonic_ns=now_ns):
            return self._retained(
                previous,
                status=SupportStatus.STALE,
                support_distance=support_distance,
                reason="affect intent expired before evaluation",
            )

        if support_distance is None:
            return self._retained(
                previous,
                status=SupportStatus.FALLBACK,
                support_distance=None,
                reason="no retained support evidence",
            )
        support_status, reason = self._classify_support(support_distance)
        if support_status is SupportStatus.FALLBACK:
            return self._retained(
                previous,
                status=support_status,
                support_distance=support_distance,
                reason=reason,
            )

        elapsed_s = (now_ns - previous.accepted_monotonic_ns) / 1_000_000_000
        time_constant_s = (
            intent.transition_duration_s
            if intent.transition_duration_s is not None
            else self._config.default_transition_time_constant_s
        )
        alpha = -math.expm1(-elapsed_s / time_constant_s)
        vector = tuple(
            old + alpha * (requested - old)
            for old, requested in zip(previous.vector, intent.vector, strict=True)
        )
        intensity = previous.intensity + alpha * (
            intent.intensity - previous.intensity
        )
        return FilteredIntent(
            schema_version="filtered-intent/v1",
            affect_schema_id=self._schema.schema_id,
            vector=vector,
            intensity=intensity,
            source_id=intent.source_id,
            source_confidence=intent.source_confidence,
            accepted_monotonic_ns=now_ns,
            support_status=support_status,
            support_distance=support_distance,
            reason=reason,
        )

    def _validate_previous(self, previous: FilteredIntent, *, now_ns: int) -> None:
        if now_ns < 0:
            raise ValueError("now_ns must be non-negative")
        if previous.affect_schema_id != self._schema.schema_id:
            raise ValueError("previous filtered intent does not match affect schema")
        if len(previous.vector) != len(self._schema.dimensions):
            raise ValueError("previous filtered intent dimension count does not match")
        if now_ns < previous.accepted_monotonic_ns:
            raise ValueError("now_ns must not precede the last accepted update")

    def _support_distance(self, vector: tuple[float, ...]) -> float | None:
        if not self._config.retained_training_coordinates:
            return None
        dimension_count = len(self._config.coordinate_scales)
        return min(
            math.sqrt(
                sum(
                    ((requested - demonstrated) / scale) ** 2
                    for requested, demonstrated, scale in zip(
                        vector,
                        coordinate,
                        self._config.coordinate_scales,
                        strict=True,
                    )
                )
                / dimension_count
            )
            for coordinate in self._config.retained_training_coordinates
        )

    def _classify_support(self, distance: float) -> tuple[SupportStatus, str]:
        if distance <= self._config.supported_max_distance:
            return SupportStatus.SUPPORTED, "within demonstrated support"
        if distance <= self._config.interpolated_max_distance:
            return SupportStatus.INTERPOLATED, "within interpolation support"
        return SupportStatus.FALLBACK, "outside demonstrated support"

    @staticmethod
    def _retained(
        previous: FilteredIntent,
        *,
        status: SupportStatus,
        support_distance: float | None,
        reason: str,
    ) -> FilteredIntent:
        return FilteredIntent(
            schema_version="filtered-intent/v1",
            affect_schema_id=previous.affect_schema_id,
            vector=previous.vector,
            intensity=previous.intensity,
            source_id=previous.source_id,
            source_confidence=previous.source_confidence,
            accepted_monotonic_ns=previous.accepted_monotonic_ns,
            support_status=status,
            support_distance=support_distance,
            reason=reason,
        )
