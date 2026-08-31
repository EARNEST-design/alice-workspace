"""MediaPipe-backed blendshape observation adapter."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence, cast

import numpy as np
from numpy.typing import NDArray

from alice.contracts.blendshapes import (
    BlendshapeObservation,
    BlendshapeScore,
    ObservationValidity,
)
from alice.perception.camera import CapturedFrame

RgbImage = NDArray[np.uint8]


class BlendshapeDetector(Protocol):
    """Minimal detector surface for injected blendshape extraction."""

    def detect_scores(
        self, rgb: RgbImage
    ) -> tuple[float | None, list[tuple[str, float]]]:
        """Return face confidence and named scores for the first detected face."""


class FaceLandmarkerLike(Protocol):
    """Minimal MediaPipe task surface used by the detector wrapper."""

    def detect(self, image: object) -> object: ...

    def close(self) -> None: ...


def _default_image_factory(rgb: RgbImage) -> object:
    import mediapipe as mp  # type: ignore[import-untyped]

    return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)


def _default_landmarker_factory(model_path: Path) -> FaceLandmarkerLike:
    from mediapipe.tasks import python  # type: ignore[import-untyped]
    from mediapipe.tasks.python import vision  # type: ignore[import-untyped]

    options = vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(model_path)),
        num_faces=1,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=False,
    )
    return cast(FaceLandmarkerLike, vision.FaceLandmarker.create_from_options(options))


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    score = getattr(value, "score", None)
    if isinstance(score, (int, float)):
        return float(score)
    return None


def _extract_face_confidence(result: object) -> float | None:
    raw_confidence = getattr(result, "face_confidence", None)
    if isinstance(raw_confidence, Sequence) and not isinstance(
        raw_confidence, (str, bytes)
    ):
        if not raw_confidence:
            return None
        return _as_float(raw_confidence[0])

    confidence = _as_float(raw_confidence)
    if confidence is not None:
        return confidence

    # The Python FaceLandmarkerResult does not expose a direct confidence field.
    face_blendshapes = getattr(result, "face_blendshapes", [])
    if face_blendshapes:
        return 1.0
    return None


def _hash_model(model_path: Path) -> str:
    digest = sha256()
    with model_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class MediaPipeTaskDetector(BlendshapeDetector):
    """Adapter over the MediaPipe Face Landmarker task API."""

    landmarker: FaceLandmarkerLike
    image_factory: Callable[[RgbImage], object] = _default_image_factory

    @classmethod
    def from_model_path(cls, model_path: Path) -> "MediaPipeTaskDetector":
        return cls(landmarker=_default_landmarker_factory(model_path))

    def detect_scores(
        self, rgb: RgbImage
    ) -> tuple[float | None, list[tuple[str, float]]]:
        result = self.landmarker.detect(self.image_factory(rgb))
        face_blendshapes = cast(
            list[list[Any]],
            getattr(result, "face_blendshapes", []),
        )
        if not face_blendshapes:
            return None, []

        categories = face_blendshapes[0]
        scores = [
            (str(category.category_name), float(category.score))
            for category in categories
        ]
        return _extract_face_confidence(result), scores

    def close(self) -> None:
        self.landmarker.close()


class MediaPipeBlendshapeAdapter:
    """Convert captured frames into versioned blendshape observations."""

    def __init__(
        self,
        *,
        camera_id: str,
        model_path: Path,
        detector: BlendshapeDetector | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.model_path = model_path
        self.detector = detector or MediaPipeTaskDetector.from_model_path(model_path)
        self._model_sha256 = _hash_model(model_path)

    def observe(self, frame: CapturedFrame, run_id: str) -> BlendshapeObservation:
        rgb = np.ascontiguousarray(frame.bgr[..., ::-1])
        face_confidence, raw_scores = self.detector.detect_scores(rgb)
        image_height, image_width = frame.bgr.shape[:2]

        if face_confidence is None or not raw_scores:
            return BlendshapeObservation(
                schema_version="blendshape-observation/v1",
                captured_at=frame.captured_at,
                monotonic_ns=frame.monotonic_ns,
                camera_id=self.camera_id,
                run_id=run_id,
                detector="mediapipe-face-landmarker",
                detector_model_sha256=self._model_sha256,
                image_width=image_width,
                image_height=image_height,
                face_confidence=None,
                validity=ObservationValidity.NO_FACE,
                invalid_reason="no face detected",
                scores=(),
            )

        scores = tuple(
            BlendshapeScore(name=name, score=score)
            for name, score in sorted(raw_scores, key=lambda item: item[0])
        )
        return BlendshapeObservation(
            schema_version="blendshape-observation/v1",
            captured_at=frame.captured_at,
            monotonic_ns=frame.monotonic_ns,
            camera_id=self.camera_id,
            run_id=run_id,
            detector="mediapipe-face-landmarker",
            detector_model_sha256=self._model_sha256,
            image_width=image_width,
            image_height=image_height,
            face_confidence=face_confidence,
            validity=ObservationValidity.VALID,
            invalid_reason=None,
            scores=scores,
        )
