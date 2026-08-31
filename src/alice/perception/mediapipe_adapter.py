"""MediaPipe-backed blendshape observation adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
from alice.perception.model_manifest import (
    sha256_path,
    validate_model_artifact,
)
from alice.perception.preview import PreviewDetection

RgbImage = NDArray[np.uint8]
DetectorFactory = Callable[[Path], "BlendshapeDetector"]


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

    return None


@dataclass
class MediaPipeTaskDetector(BlendshapeDetector):
    """Adapter over the MediaPipe Face Landmarker task API."""

    landmarker: FaceLandmarkerLike
    image_factory: Callable[[RgbImage], object] = _default_image_factory

    @classmethod
    def from_model_path(
        cls,
        model_path: Path,
        *,
        landmarker_factory: Callable[[Path], FaceLandmarkerLike] = (
            _default_landmarker_factory
        ),
    ) -> "MediaPipeTaskDetector":
        validate_model_artifact(model_path)
        return cls(landmarker=landmarker_factory(model_path))

    def detect_scores(
        self, rgb: RgbImage
    ) -> tuple[float | None, list[tuple[str, float]]]:
        preview = self.detect_preview(rgb)
        return preview.face_confidence, list(preview.scores)

    def detect_preview(self, rgb: RgbImage) -> PreviewDetection:
        result = self.landmarker.detect(self.image_factory(rgb))
        face_blendshapes = cast(
            list[list[Any]],
            getattr(result, "face_blendshapes", []),
        )
        face_landmarks = cast(list[list[Any]], getattr(result, "face_landmarks", []))
        scores: tuple[tuple[str, float], ...] = ()
        if face_blendshapes:
            categories = face_blendshapes[0]
            scores = tuple(
                (str(category.category_name), float(category.score))
                for category in categories
            )
        landmarks: tuple[tuple[float, float], ...] = ()
        if face_landmarks:
            landmarks = tuple(
                (float(landmark.x), float(landmark.y)) for landmark in face_landmarks[0]
            )
        return PreviewDetection(
            face_confidence=_extract_face_confidence(result),
            scores=scores,
            landmarks=landmarks,
        )

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
        detector_factory: DetectorFactory | None = None,
        model_hasher: Callable[[Path], str] = sha256_path,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.model_path = model_path
        owns_detector = detector is None
        model_sha256 = model_hasher(model_path)
        if detector is None:
            model_sha256 = validate_model_artifact(model_path)
            factory = detector_factory or _verified_detector_factory
            created_detector = factory(model_path)
        else:
            created_detector = detector
        self.detector = created_detector
        self._owns_detector = owns_detector
        self._model_sha256 = model_sha256
        self._now = now or (lambda: datetime.now(UTC))

    def observe(self, frame: CapturedFrame, run_id: str) -> BlendshapeObservation:
        rgb = np.ascontiguousarray(frame.bgr[..., ::-1])
        face_confidence, raw_scores = self.detector.detect_scores(rgb)
        image_height, image_width = frame.bgr.shape[:2]

        if not raw_scores:
            return BlendshapeObservation(
                schema_version="blendshape-observation/v1",
                captured_at=frame.captured_at,
                observed_at=self._now(),
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
            observed_at=self._now(),
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

    def close(self) -> None:
        """Release the underlying MediaPipe task when this adapter owns it."""

        if self._owns_detector:
            _close_detector(self.detector)


def _close_detector(detector: BlendshapeDetector) -> None:
    close = getattr(detector, "close", None)
    if callable(close):
        close()


def _verified_detector_factory(model_path: Path) -> BlendshapeDetector:
    """Construct a landmarker only after the adapter verified its hash."""

    return MediaPipeTaskDetector(landmarker=_default_landmarker_factory(model_path))
