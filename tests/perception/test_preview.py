from datetime import UTC, datetime

import numpy as np

from alice.perception.camera import CapturedFrame
from alice.perception.preview import PreviewDetection, run_preview


class FakeCamera:
    def __init__(self, frames: list[CapturedFrame]) -> None:
        self._frames = frames
        self._index = 0
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def read(self) -> CapturedFrame:
        frame = self._frames[self._index]
        self._index += 1
        return frame

    def close(self) -> None:
        self.closed = True


class FakePreviewDetector:
    def __init__(self, detections: list[PreviewDetection]) -> None:
        self._detections = detections
        self._index = 0
        self.closed = False
        self.rgb_inputs: list[np.ndarray] = []

    def detect_preview(self, rgb: np.ndarray) -> PreviewDetection:
        self.rgb_inputs.append(rgb.copy())
        detection = self._detections[self._index]
        self._index += 1
        return detection

    def close(self) -> None:
        self.closed = True


class FakeDrawer:
    def __init__(self) -> None:
        self.mesh_calls: list[tuple[tuple[float, float], ...]] = []
        self.score_calls: list[tuple[tuple[str, float], ...]] = []
        self.status_calls: list[str] = []

    def draw_face_mesh(
        self,
        _frame: np.ndarray,
        landmarks: tuple[tuple[float, float], ...],
    ) -> None:
        self.mesh_calls.append(landmarks)

    def draw_scores(
        self,
        _frame: np.ndarray,
        scores: tuple[tuple[str, float], ...],
    ) -> None:
        self.score_calls.append(scores)

    def draw_status(self, _frame: np.ndarray, status: str) -> None:
        self.status_calls.append(status)


class FakeWindow:
    def __init__(self, keys: list[int]) -> None:
        self._keys = keys
        self._index = 0
        self.shown: list[tuple[str, np.ndarray]] = []
        self.destroyed_titles: list[str] = []

    def show(self, title: str, frame: np.ndarray) -> None:
        self.shown.append((title, frame.copy()))

    def wait_key(self, _delay_ms: int) -> int:
        key = self._keys[self._index]
        self._index += 1
        return key

    def destroy(self, title: str) -> None:
        self.destroyed_titles.append(title)


def _frame(fill_value: int) -> CapturedFrame:
    return CapturedFrame(
        captured_at=datetime(2026, 8, 31, tzinfo=UTC),
        monotonic_ns=42 + fill_value,
        bgr=np.full((3, 4, 3), fill_value=fill_value, dtype=np.uint8),
    )


def test_run_preview_draws_mesh_and_top_scores_until_q() -> None:
    camera = FakeCamera([_frame(7)])
    detector = FakePreviewDetector(
        [
            PreviewDetection(
                face_confidence=0.88,
                scores=(
                    ("jawOpen", 0.2),
                    ("eyeBlinkLeft", 0.1),
                    ("browInnerUp", 0.3),
                ),
                landmarks=((0.25, 0.5), (0.75, 0.5)),
            )
        ]
    )
    drawer = FakeDrawer()
    window = FakeWindow([ord("q")])

    run_preview(
        camera,
        detector,
        drawer=drawer,
        window=window,
        top_score_count=2,
        window_title="Alice Preview",
    )

    assert camera.opened is True
    assert camera.closed is True
    assert detector.closed is True
    assert drawer.mesh_calls == [((0.25, 0.5), (0.75, 0.5))]
    assert drawer.score_calls == [(
        ("browInnerUp", 0.3),
        ("jawOpen", 0.2),
    )]
    assert drawer.status_calls == ["face confidence: 0.880"]
    assert window.destroyed_titles == ["Alice Preview"]
    assert window.shown[0][0] == "Alice Preview"
    assert detector.rgb_inputs[0][0, 0].tolist() == [7, 7, 7]


def test_run_preview_draws_no_face_status_when_detector_has_no_scores() -> None:
    camera = FakeCamera([_frame(3)])
    detector = FakePreviewDetector(
        [PreviewDetection(face_confidence=None, scores=(), landmarks=())]
    )
    drawer = FakeDrawer()
    window = FakeWindow([ord("q")])

    run_preview(camera, detector, drawer=drawer, window=window)

    assert drawer.mesh_calls == []
    assert drawer.score_calls == []
    assert drawer.status_calls == ["No face detected"]
