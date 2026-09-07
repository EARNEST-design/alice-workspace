"""Blendshape observation contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Sequence

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Sha256Hex = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
UnitIntervalFloat = Annotated[float, Field(ge=0.0, le=1.0)]


class ObservationValidity(StrEnum):
    VALID = "valid"
    NO_FACE = "no_face"


class BlendshapeScore(BaseModel):
    """A named detector output with a normalized score."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonEmptyString
    score: UnitIntervalFloat


class BlendshapeObservation(BaseModel):
    """A versioned, hardware-independent blendshape observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["blendshape-observation/v1"]
    captured_at: AwareDatetime
    observed_at: AwareDatetime | None = None
    monotonic_ns: Annotated[int, Field(ge=0)]
    camera_id: NonEmptyString
    run_id: NonEmptyString
    detector: NonEmptyString
    detector_model_sha256: Sha256Hex
    image_width: Annotated[int, Field(gt=0)]
    image_height: Annotated[int, Field(gt=0)]
    face_confidence: UnitIntervalFloat | None
    validity: ObservationValidity
    invalid_reason: NonEmptyString | None
    scores: tuple[BlendshapeScore, ...]

    @model_validator(mode="after")
    def validate_consistency(self) -> BlendshapeObservation:
        names = [score.name for score in self.scores]
        if len(names) != len(set(names)):
            raise ValueError("scores must contain unique category names")

        if self.validity is ObservationValidity.VALID:
            if self.invalid_reason is not None:
                raise ValueError("invalid_reason must be None when validity is valid")
            if not self.scores:
                raise ValueError("scores must be present when validity is valid")
            return self

        if self.invalid_reason is None:
            raise ValueError("invalid_reason is required when validity is not valid")
        if self.face_confidence is not None:
            raise ValueError("face_confidence must be None when validity is not valid")
        if self.scores:
            raise ValueError("scores must be empty when validity is not valid")
        return self


def validate_category_schema(
    observation: BlendshapeObservation,
    expected_names: Sequence[str],
) -> None:
    """Validate that an observation exposes the exact expected category names."""

    if len(expected_names) != len(set(expected_names)):
        raise ValueError("expected_names must not contain duplicate category names")

    observed_names = {score.name for score in observation.scores}
    expected_name_set = set(expected_names)

    if observed_names == expected_name_set:
        return

    missing = sorted(expected_name_set - observed_names)
    unexpected = sorted(observed_names - expected_name_set)
    raise ValueError(
        "Blendshape category schema mismatch: "
        f"missing={missing} unexpected={unexpected}"
    )
