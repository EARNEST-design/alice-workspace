"""Camera adapter boundaries for passive perception."""

from __future__ import annotations

import os
import re
import subprocess
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
_V4L2_DEVICE_PATTERN = re.compile(
    r"^/dev/(?:video\d+|v4l/by-(?:id|path)/[^/\s]+-video-index\d+)$"
)


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
    capability_error: str | None = None


class CameraProbeError(RuntimeError):
    """Raised when read-only camera capability probing fails."""


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
CapabilityProbe = Callable[[str], tuple[str, ...]]


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
        try:
            is_opened = capture.isOpened()
        except Exception:
            capture.release()
            raise
        if not is_opened:
            capture.release()
            raise RuntimeError(f"failed to open camera {self.camera_id!r}")

        try:
            if self.width is not None:
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.width))
            if self.height is not None:
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.height))
            if self.fps is not None:
                capture.set(cv2.CAP_PROP_FPS, self.fps)
        except Exception:
            capture.release()
            raise

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
    capability_probe: CapabilityProbe | None = None,
) -> list[CameraInfo]:
    """Discover camera device nodes without opening them."""

    probe = capability_probe or probe_camera_capabilities
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
            capabilities, capability_error = _probe_capabilities(device, probe)
            cameras.append(
                CameraInfo(
                    camera_id=stable_name,
                    device=device,
                    label=stable_name,
                    capabilities=capabilities,
                    capability_error=capability_error,
                )
            )
        return cameras

    return [
        _camera_info_from_device(device, probe)
        for device in sorted(by_id_glob("/dev/video*"))
    ]


def validate_camera_device_selector(device: str) -> str:
    """Accept only explicit V4L2 video selectors."""

    if not _V4L2_DEVICE_PATTERN.fullmatch(device):
        raise ValueError(
            "camera_device must be an explicit V4L2 video selector under "
            "/dev/videoN, /dev/v4l/by-id/, or /dev/v4l/by-path/"
        )
    return device


def _camera_info_from_device(
    device: str,
    probe: CapabilityProbe,
) -> CameraInfo:
    capabilities, capability_error = _probe_capabilities(device, probe)
    return CameraInfo(
        camera_id=Path(device).name,
        device=device,
        label=Path(device).name,
        capabilities=capabilities,
        capability_error=capability_error,
    )


def _probe_capabilities(
    device: str,
    capability_probe: CapabilityProbe,
) -> tuple[tuple[str, ...], str | None]:
    try:
        return capability_probe(device), None
    except CameraProbeError as error:
        return (), str(error)


def parse_v4l2_capabilities(output: str) -> tuple[str, ...]:
    """Parse `v4l2-ctl --list-formats-ext` output into `WIDTHxHEIGHT@FPS` modes."""

    size_pattern = re.compile(r"Size:\s+Discrete\s+(\d+)x(\d+)")
    interval_pattern = re.compile(r"Interval:\s+Discrete\s+([0-9.]+)s")
    current_size: tuple[str, str] | None = None
    modes: list[str] = []

    for line in output.splitlines():
        size_match = size_pattern.search(line)
        if size_match is not None:
            current_size = (size_match.group(1), size_match.group(2))
            continue

        interval_match = interval_pattern.search(line)
        if interval_match is None or current_size is None:
            continue

        seconds = float(interval_match.group(1))
        if seconds <= 0.0:
            continue
        width, height = current_size
        fps = round(1.0 / seconds)
        modes.append(f"{width}x{height}@{fps}")

    return tuple(dict.fromkeys(modes))


def probe_camera_capabilities(device: str) -> tuple[str, ...]:
    """Read camera supported modes via a read-only `v4l2-ctl` probe."""

    try:
        result = subprocess.run(
            ["v4l2-ctl", "--list-formats-ext", "--device", device],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise CameraProbeError("v4l2-ctl unavailable") from error
    except subprocess.CalledProcessError as error:
        stderr = error.stderr.strip() or error.stdout.strip() or "probe failed"
        raise CameraProbeError(stderr) from error

    capabilities = parse_v4l2_capabilities(result.stdout)
    if capabilities:
        return capabilities
    raise CameraProbeError("no supported modes reported")
