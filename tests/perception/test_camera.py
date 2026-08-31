from datetime import UTC, datetime

import numpy as np
import pytest

from alice.perception.camera import (
    CameraInfo,
    CameraProbeError,
    OpenCVCamera,
    list_cameras,
)


class FakeCapture:
    def __init__(
        self,
        *,
        opened: bool = True,
        read_result: tuple[bool, np.ndarray | None] = (
            True,
            np.ones((2, 3, 3), dtype=np.uint8),
        ),
    ) -> None:
        self._opened = opened
        self._read_result = read_result
        self.settings: list[tuple[int, float]] = []
        self.released = False

    def isOpened(self) -> bool:
        return self._opened

    def read(self) -> tuple[bool, np.ndarray | None]:
        return self._read_result

    def release(self) -> None:
        self.released = True

    def set(self, prop_id: int, value: float) -> bool:
        self.settings.append((prop_id, value))
        return True


def test_camera_does_not_open_device_until_explicit_open() -> None:
    opened_devices: list[str | int] = []

    def capture_factory(device: str | int) -> FakeCapture:
        opened_devices.append(device)
        return FakeCapture()

    camera = OpenCVCamera(
        camera_id="alice-face-webcam",
        device=0,
        capture_factory=capture_factory,
    )

    assert opened_devices == []

    camera.open()

    assert opened_devices == [0]


def test_camera_open_raises_when_capture_cannot_open() -> None:
    camera = OpenCVCamera(
        camera_id="alice-face-webcam",
        device="/dev/video0",
        capture_factory=lambda _device: FakeCapture(opened=False),
    )

    with pytest.raises(RuntimeError, match="open camera"):
        camera.open()


def test_camera_read_requires_open() -> None:
    camera = OpenCVCamera(camera_id="alice-face-webcam", device="/dev/video0")

    with pytest.raises(RuntimeError, match="before open"):
        camera.read()


def test_camera_read_returns_captured_frame() -> None:
    expected_time = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    expected_frame = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
    capture = FakeCapture(read_result=(True, expected_frame))
    camera = OpenCVCamera(
        camera_id="alice-face-webcam",
        device="/dev/video0",
        capture_factory=lambda _device: capture,
        now=lambda: expected_time,
        monotonic_ns=lambda: 42,
    )

    camera.open()
    frame = camera.read()

    assert frame.captured_at == expected_time
    assert frame.monotonic_ns == 42
    assert np.array_equal(frame.bgr, expected_frame)
    camera.close()
    assert capture.released is True


def test_camera_read_raises_when_capture_returns_no_frame() -> None:
    camera = OpenCVCamera(
        camera_id="alice-face-webcam",
        device="/dev/video0",
        capture_factory=lambda _device: FakeCapture(read_result=(False, None)),
    )
    camera.open()

    with pytest.raises(RuntimeError, match="capture frame"):
        camera.read()


def test_list_cameras_populates_injected_capabilities() -> None:
    cameras = list_cameras(
        by_id_glob=lambda pattern: ["/dev/v4l/by-id/usb-Logitech"],
        resolve_path=lambda path: "/dev/video2",
        capability_probe=lambda device: ("640x480@30", "1280x720@30"),
    )

    assert cameras == [
        CameraInfo(
            camera_id="usb-Logitech",
            device="/dev/video2",
            label="usb-Logitech",
            capabilities=("640x480@30", "1280x720@30"),
            capability_error=None,
        )
    ]


def test_list_cameras_reports_probe_failure_honestly() -> None:
    def probe(_device: str) -> tuple[str, ...]:
        raise CameraProbeError("v4l2-ctl unavailable")

    cameras = list_cameras(
        by_id_glob=lambda pattern: ["/dev/video0"] if pattern == "/dev/video*" else [],
        capability_probe=probe,
    )

    assert cameras == [
        CameraInfo(
            camera_id="video0",
            device="/dev/video0",
            label="video0",
            capabilities=(),
            capability_error="v4l2-ctl unavailable",
        )
    ]
