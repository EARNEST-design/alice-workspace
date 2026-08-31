"""Camera adapter boundaries for passive perception."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from glob import glob
from pathlib import Path
from typing import Callable, Protocol, cast

import cv2
import numpy as np
from numpy.typing import NDArray

FrameArray = NDArray[np.uint8]


@dataclass(frozen=True)
class CapturedFrame:
    """A single captured frame with wall-clock and monotonic timing."""

    captured_at: datetime
    monotonic_ns: int
    bgr: FrameArray


@dataclass(frozen=True)
class CameraInfo:
    """Read-only camera discovery information."""

    camera_id: str
    device: str
    label: str
    capabilities: tuple[str, ...] = ()


class FrameSource(Protocol):
    """A passive frame source that emits timestamped frames."""

    def read(self) -> CapturedFrame:
        """Capture one frame."""


class VideoCaptureLike(Protocol):
    """Minimal OpenCV capture surface used by the adapter."""

    def isOpened(self) -> bool: ...

    def read(self) -> tuple[bool, FrameArray | None]: ...

    def release(self) -> None: ...

    def set(self, prop_id: int, value: float) -> bool: ...


CaptureFactory = Callable[[str | int], VideoCaptureLike]


class OpenCVCamera(FrameSource):
    """OpenCV-backed camera with explicit device access in open()."""

    def __init__(
        self,
        *,
        camera_id: str,
        device: str | int,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
        capture_factory: CaptureFactory | None = None,
        now: Callable[[], datetime] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self._capture_factory = (
            capture_factory or cast(CaptureFactory, cv2.VideoCapture)
        )
        self._now = now or (lambda: datetime.now(UTC))
        self._monotonic_ns = monotonic_ns or time.monotonic_ns
        self._capture: VideoCaptureLike | None = None

    def open(self) -> None:
        """Open the configured device explicitly."""

        if self._capture is not None:
            return

        capture = self._capture_factory(self.device)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"failed to open camera {self.camera_id!r}")

        if self.width is not None:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.width))
        if self.height is not None:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.height))
        if self.fps is not None:
            capture.set(cv2.CAP_PROP_FPS, self.fps)

        self._capture = capture

    def read(self) -> CapturedFrame:
        """Read one frame from the opened camera."""

        if self._capture is None:
            raise RuntimeError("camera must be opened before open-dependent reads")

        ok, frame = self._capture.read()
        if not ok or frame is None:
            raise RuntimeError(
                f"failed to capture frame from camera {self.camera_id!r}"
            )

        return CapturedFrame(
            captured_at=self._now(),
            monotonic_ns=self._monotonic_ns(),
            bgr=np.asarray(frame).copy(),
        )

    def close(self) -> None:
        """Release the underlying capture device if it is open."""

        if self._capture is None:
            return
        self._capture.release()
        self._capture = None


def list_cameras(
    *,
    by_id_glob: Callable[[str], list[str]] = glob,
    resolve_path: Callable[[str], str] = os.path.realpath,
) -> list[CameraInfo]:
    """Discover camera device nodes without opening them."""

    by_id_paths = sorted(by_id_glob("/dev/v4l/by-id/*"))
    if by_id_paths:
        seen_devices: set[str] = set()
        cameras: list[CameraInfo] = []
        for stable_path in by_id_paths:
            device = resolve_path(stable_path)
            if device in seen_devices:
                continue
            seen_devices.add(device)
            stable_name = Path(stable_path).name
            cameras.append(
                CameraInfo(
                    camera_id=stable_name,
                    device=device,
                    label=stable_name,
                )
            )
        return cameras

    return [
        CameraInfo(
            camera_id=Path(device).name,
            device=device,
            label=Path(device).name,
        )
        for device in sorted(by_id_glob("/dev/video*"))
    ]
