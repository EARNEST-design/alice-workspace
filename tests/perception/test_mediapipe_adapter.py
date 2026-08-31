import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from alice.contracts import ObservationValidity
from alice.perception.camera import CapturedFrame
from alice.perception.mediapipe_adapter import (
    MediaPipeBlendshapeAdapter,
    MediaPipeTaskDetector,
)


class FakeDetector:
    def detect_scores(
        self, _rgb: np.ndarray
    ) -> tuple[float | None, list[tuple[str, float]]]:
        return 0.88, [("jawOpen", 0.2), ("eyeBlinkLeft", 0.1)]


class NoFaceDetector:
    def detect_scores(
        self, _rgb: np.ndarray
    ) -> tuple[float | None, list[tuple[str, float]]]:
        return None, []


class FakeLandmarker:
    def __init__(self, result: object) -> None:
        self.result = result

    def detect(self, _image: object) -> object:
        return self.result


class FakeImageFactory:
    def __init__(self) -> None:
        self.rgb: np.ndarray | None = None

    def __call__(self, rgb: np.ndarray) -> np.ndarray:
        self.rgb = rgb
        return rgb


def _to_namespace(value: object) -> object:
    if isinstance(value, dict):
        return SimpleNamespace(
            **{key: _to_namespace(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return [_to_namespace(item) for item in value]
    return value


def test_adapter_preserves_names_and_sorts_canonical_schema(tmp_path: Path) -> None:
    model = tmp_path / "face.task"
    model.write_bytes(b"fixture-model")
    adapter = MediaPipeBlendshapeAdapter(
        camera_id="alice-face-webcam",
        model_path=model,
        detector=FakeDetector(),
    )
    frame = CapturedFrame(
        captured_at=datetime(2026, 8, 31, tzinfo=UTC),
        monotonic_ns=42,
        bgr=np.zeros((2, 3, 3), dtype=np.uint8),
    )

    observation = adapter.observe(frame, run_id="passive-001")

    assert [item.name for item in observation.scores] == ["eyeBlinkLeft", "jawOpen"]
    assert observation.image_width == 3
    assert observation.image_height == 2
    assert observation.detector_model_sha256 != ""


def test_adapter_emits_no_face_without_stale_scores(tmp_path: Path) -> None:
    model = tmp_path / "face.task"
    model.write_bytes(b"fixture-model")
    adapter = MediaPipeBlendshapeAdapter(
        camera_id="alice-face-webcam",
        model_path=model,
        detector=NoFaceDetector(),
    )
    frame = CapturedFrame(
        captured_at=datetime(2026, 8, 31, tzinfo=UTC),
        monotonic_ns=42,
        bgr=np.ones((4, 5, 3), dtype=np.uint8),
    )

    observation = adapter.observe(frame, run_id="passive-001")

    assert observation.validity is ObservationValidity.NO_FACE
    assert observation.invalid_reason == "no face detected"
    assert observation.face_confidence is None
    assert observation.scores == ()
    assert observation.image_width == 5
    assert observation.image_height == 4


def test_task_detector_reads_fixture_result_shape() -> None:
    fixture_path = Path("tests/fixtures/perception/face_result.json")
    result = _to_namespace(json.loads(fixture_path.read_text()))
    image_factory = FakeImageFactory()
    detector = MediaPipeTaskDetector(
        landmarker=FakeLandmarker(result),
        image_factory=image_factory,
    )
    bgr = np.array([[[0, 1, 2]]], dtype=np.uint8)

    confidence, scores = detector.detect_scores(bgr[..., ::-1].copy())

    assert confidence == 0.88
    assert scores == [("jawOpen", 0.2), ("eyeBlinkLeft", 0.1)]
    assert image_factory.rgb is not None
