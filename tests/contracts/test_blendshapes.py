from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from alice.contracts.blendshapes import (
    BlendshapeObservation,
    BlendshapeScore,
    ObservationValidity,
    validate_category_schema,
)


def valid_observation() -> BlendshapeObservation:
    return BlendshapeObservation(
        schema_version="blendshape-observation/v1",
        captured_at=datetime(2026, 8, 31, tzinfo=UTC),
        monotonic_ns=10,
        camera_id="alice-face-webcam",
        run_id="passive-001",
        detector="mediapipe-face-landmarker",
        detector_model_sha256="a" * 64,
        image_width=640,
        image_height=480,
        face_confidence=0.9,
        validity=ObservationValidity.VALID,
        invalid_reason=None,
        scores=(BlendshapeScore(name="eyeBlinkLeft", score=0.1),),
    )


def test_observation_rejects_duplicate_category_names() -> None:
    source = valid_observation().model_dump()
    source["scores"] = [
        {"name": "eyeBlinkLeft", "score": 0.1},
        {"name": "eyeBlinkLeft", "score": 0.2},
    ]
    with pytest.raises(ValidationError, match="unique"):
        BlendshapeObservation.model_validate(source)


def test_invalid_observation_requires_reason() -> None:
    source = valid_observation().model_dump()
    source.update(
        validity="no_face",
        face_confidence=None,
        invalid_reason=None,
        scores=[],
    )
    with pytest.raises(ValidationError, match="invalid_reason"):
        BlendshapeObservation.model_validate(source)


def test_category_schema_is_order_independent_but_name_exact() -> None:
    observation = valid_observation()
    validate_category_schema(observation, ("eyeBlinkLeft",))
    with pytest.raises(ValueError, match="missing=.*jawOpen"):
        validate_category_schema(observation, ("eyeBlinkLeft", "jawOpen"))
