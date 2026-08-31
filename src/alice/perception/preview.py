"""Headless-testable camera preview utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import numpy as np


@dataclass(frozen=True)
class PreviewDetection:
    """Overlay information for one preview frame."""

    face_confidence: float | None
    scores: tuple[tuple[str, float], ...]
    landmarks: tuple[tuple[float, float], ...]


class PreviewDetector(Protocol):
    """Minimal preview detector surface."""

    def detect_preview(self, rgb: np.ndarray) -> PreviewDetection: ...

    def close(self) -> None: ...


class PreviewWindow(Protocol):
    """Minimal preview window surface."""

    def show(self, title: str, frame: np.ndarray) -> None: ...

    def wait_key(self, delay_ms: int) -> int: ...

    def destroy(self, title: str) -> None: ...


class PreviewDrawer(Protocol):
    """Overlay drawing boundary for preview tests."""

    def draw_face_mesh(
        self,
        frame: np.ndarray,
        landmarks: tuple[tuple[float, float], ...],
    ) -> None: ...

    def draw_scores(
        self,
        frame: np.ndarray,
        scores: tuple[tuple[str, float], ...],
    ) -> None: ...

    def draw_status(self, frame: np.ndarray, status: str) -> None: ...


class OpenCVPreviewWindow:
    """OpenCV-backed preview window."""

    def show(self, title: str, frame: np.ndarray) -> None:
        cv2.imshow(title, frame)

    def wait_key(self, delay_ms: int) -> int:
        return int(cv2.waitKey(delay_ms))

    def destroy(self, title: str) -> None:
        cv2.destroyWindow(title)


class OpenCVPreviewDrawer:
    """OpenCV drawing helpers for preview overlays."""

    def draw_face_mesh(
        self,
        frame: np.ndarray,
        landmarks: tuple[tuple[float, float], ...],
    ) -> None:
        height, width = frame.shape[:2]
        for x_norm, y_norm in landmarks:
            x = max(0, min(width - 1, int(round(x_norm * (width - 1)))))
            y = max(0, min(height - 1, int(round(y_norm * (height - 1)))))
            cv2.circle(frame, (x, y), 1, (0, 255, 0), thickness=-1)

    def draw_scores(
        self,
        frame: np.ndarray,
        scores: tuple[tuple[str, float], ...],
    ) -> None:
        for index, (name, score) in enumerate(scores):
            cv2.putText(
                frame,
                f"{name}: {score:.3f}",
                (8, 18 + index * 18),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
                cv2.LINE_AA,
            )

    def draw_status(self, frame: np.ndarray, status: str) -> None:
        cv2.putText(
            frame,
            status,
            (8, frame.shape[0] - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )


def run_preview(
    frame_source: Any,
    detector: PreviewDetector,
    *,
    drawer: PreviewDrawer | None = None,
    window: PreviewWindow | None = None,
    top_score_count: int = 5,
    key_delay_ms: int = 1,
    window_title: str = "alice-camera preview",
    owns_resources: bool = True,
) -> None:
    """Render a live preview, closing inputs by default, until q is pressed."""

    preview_drawer = drawer or OpenCVPreviewDrawer()
    preview_window = window or OpenCVPreviewWindow()
    body_error: BaseException | None = None
    should_destroy_window = False
    try:
        open_camera = getattr(frame_source, "open", None)
        if callable(open_camera):
            open_camera()
        should_destroy_window = True
        while True:
            frame = frame_source.read()
            preview_frame = np.asarray(frame.bgr).copy()
            detection = detector.detect_preview(
                np.ascontiguousarray(frame.bgr[..., ::-1])
            )
            if detection.landmarks:
                preview_drawer.draw_face_mesh(preview_frame, detection.landmarks)
            top_scores = tuple(
                sorted(detection.scores, key=lambda item: item[1], reverse=True)[
                    :top_score_count
                ]
            )
            if top_scores:
                preview_drawer.draw_scores(preview_frame, top_scores)
                status = (
                    f"face confidence: {detection.face_confidence:.3f}"
                    if detection.face_confidence is not None
                    else "face detected"
                )
            else:
                status = "No face detected"
            preview_drawer.draw_status(preview_frame, status)
            preview_window.show(window_title, preview_frame)
            if preview_window.wait_key(key_delay_ms) in (ord("q"), ord("Q")):
                break
    except BaseException as error:
        body_error = error
    close_error = _close_preview_resources(
        frame_source if owns_resources else None,
        detector if owns_resources else None,
        preview_window if should_destroy_window else None,
        window_title=window_title,
    )
    if body_error is not None:
        if close_error is not None:
            body_error.add_note(f"cleanup failed: {close_error}")
        raise body_error
    if close_error is not None:
        raise close_error


def _close_preview_resources(
    frame_source: Any,
    detector: PreviewDetector | None,
    window: PreviewWindow | None,
    *,
    window_title: str,
) -> BaseException | None:
    first_error: BaseException | None = None
    for resource in (frame_source, detector):
        close = getattr(resource, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except BaseException as error:
            if first_error is None:
                first_error = error
    if window is not None:
        try:
            window.destroy(window_title)
        except BaseException as error:
            if first_error is None:
                first_error = error
    return first_error
