"""Versioned contracts for continuous affect intent."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from alice.contracts.blendshapes import NonEmptyString

AffectCoordinate = Annotated[
    float,
    Field(ge=-1.0, le=1.0, allow_inf_nan=False),
]
UnitIntervalFloat = Annotated[
    float,
    Field(ge=0.0, le=1.0, allow_inf_nan=False),
]
MonotonicNanoseconds = Annotated[int, Field(ge=0)]
PositiveSeconds = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]


class AffectVectorSchema(BaseModel):
    """Identity and dimension order for one continuous affect-vector version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: NonEmptyString
    dimensions: tuple[NonEmptyString, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dimensions(self) -> AffectVectorSchema:
        if len(self.dimensions) != len(set(self.dimensions)):
            raise ValueError("affect dimensions must be unique")
        return self

    def validate_intent(self, intent: AffectIntent) -> None:
        """Require an intent to use this identity and ordered vector width."""

        if intent.affect_schema_id != self.schema_id:
            raise ValueError("affect schema identity does not match")
        if len(intent.vector) != len(self.dimensions):
            raise ValueError("affect vector dimension count does not match schema")


class AffectIntent(BaseModel):
    """A bounded-lifetime continuous affect request from an identified source.

    ``cluster_labels`` are descriptive annotations only. Consumers use
    ``affect_schema_id`` and ``vector`` as the authoritative affect value.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["affect-intent/v1"]
    affect_schema_id: NonEmptyString
    vector: tuple[AffectCoordinate, ...] = Field(min_length=1)
    intensity: UnitIntervalFloat
    source_id: NonEmptyString
    source_confidence: UnitIntervalFloat | None = None
    captured_at: AwareDatetime
    received_monotonic_ns: MonotonicNanoseconds
    expires_monotonic_ns: MonotonicNanoseconds
    transition_duration_s: PositiveSeconds | None = None
    cluster_labels: tuple[NonEmptyString, ...] = Field(
        default=(),
        description=(
            "Optional descriptive metadata; never authoritative categorical labels."
        ),
    )

    @model_validator(mode="after")
    def validate_validity_window(self) -> AffectIntent:
        if self.expires_monotonic_ns <= self.received_monotonic_ns:
            raise ValueError("affect intent must expire after receipt")
        return self

    def is_expired(self, *, now_monotonic_ns: int) -> bool:
        """Return true at or after the exclusive intent deadline."""

        return now_monotonic_ns >= self.expires_monotonic_ns
