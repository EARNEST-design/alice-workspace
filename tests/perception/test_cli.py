import builtins
import importlib
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from alice.experiments.passive_capture import PassiveCaptureConfig
from alice.perception.camera import CameraInfo
from alice.perception.cli import main


def test_list_command_reports_injected_camera_capabilities() -> None:
    output = io.StringIO()

    exit_code = main(
        ["list"],
        stdout=output,
        enumerate_cameras=lambda: [
            CameraInfo(
                camera_id="alice-face-webcam",
                device="/dev/video0",
                label="USB Camera",
                capabilities=("640x480@30", "1280x720@30"),
                capability_error=None,
            )
        ],
    )

    assert exit_code == 0
    assert "alice-face-webcam" in output.getvalue()
    assert "/dev/video0" in output.getvalue()
    assert "1280x720@30" in output.getvalue()


def test_list_command_reports_probe_failure_honestly() -> None:
    output = io.StringIO()

    exit_code = main(
        ["list"],
        stdout=output,
        enumerate_cameras=lambda: [
            CameraInfo(
                camera_id="alice-face-webcam",
                device="/dev/video0",
                label="USB Camera",
                capabilities=(),
                capability_error="v4l2-ctl unavailable",
            )
        ],
    )

    assert exit_code == 0
    assert "v4l2-ctl unavailable" in output.getvalue()


def test_cli_module_import_does_not_import_serial_or_actuator_modules(
    monkeypatch,
) -> None:
    forbidden_imports: list[str] = []
    real_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "serial" or name.startswith("alice.act"):
            forbidden_imports.append(name)
            raise AssertionError(f"unexpected import: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sys.modules.pop("alice.perception.cli", None)
    sys.modules.pop("alice.perception.camera", None)

    importlib.import_module("alice.perception.cli")

    assert forbidden_imports == []


class FakeCamera:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True


class FakeObserver:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _write_capture_config(path: Path) -> None:
    path.write_text(
        """\
run_id: pilot-template
camera_id: alice-face-webcam
camera_device: /dev/v4l/by-id/usb-Alice-video-index0
requested_width: 640
requested_height: 480
requested_fps: 10
duration_seconds: 1
sample_count: 1
sample_interval_ms: 0
retain_frames: false
""",
        encoding="utf-8",
    )


def test_capture_command_wires_yaml_camera_model_and_run_store(tmp_path: Path) -> None:
    config_path = tmp_path / "capture.yaml"
    model_path = tmp_path / "face_landmarker.task"
    output_dir = tmp_path / "run"
    _write_capture_config(config_path)
    model_path.write_bytes(b"model")
    cameras: list[FakeCamera] = []
    observers: list[FakeObserver] = []
    captured: list[tuple[PassiveCaptureConfig, object, object, Path]] = []

    def camera_factory(**kwargs: object) -> FakeCamera:
        camera = FakeCamera(**kwargs)
        cameras.append(camera)
        return camera

    def observer_factory(**kwargs: object) -> FakeObserver:
        observer = FakeObserver(**kwargs)
        observers.append(observer)
        return observer

    def capture_runner(
        config: PassiveCaptureConfig,
        camera: object,
        observer: object,
        output: Path,
    ) -> object:
        captured.append((config, camera, observer, output))
        return SimpleNamespace(run_id=config.run_id, status="completed")

    stdout = io.StringIO()
    exit_code = main(
        [
            "capture",
            str(config_path),
            str(output_dir),
            "--model-path",
            str(model_path),
            "--run-id",
            "pilot-repeat-1",
        ],
        stdout=stdout,
        camera_factory=camera_factory,
        observer_factory=observer_factory,
        capture_runner=capture_runner,
    )

    assert exit_code == 0
    assert len(cameras) == 1
    assert cameras[0].kwargs == {
        "camera_id": "alice-face-webcam",
        "device": "/dev/v4l/by-id/usb-Alice-video-index0",
        "width": 640,
        "height": 480,
        "fps": 10,
    }
    assert cameras[0].opened is True
    assert cameras[0].closed is True
    assert len(observers) == 1
    assert observers[0].kwargs == {
        "camera_id": "alice-face-webcam",
        "model_path": model_path,
    }
    assert observers[0].closed is True
    assert captured == [
        (
            PassiveCaptureConfig.model_validate(
                {
                    "run_id": "pilot-repeat-1",
                    "camera_id": "alice-face-webcam",
                    "camera_device": "/dev/v4l/by-id/usb-Alice-video-index0",
                    "requested_width": 640,
                    "requested_height": 480,
                    "requested_fps": 10,
                    "duration_seconds": 1,
                    "sample_count": 1,
                    "sample_interval_ms": 0,
                    "retain_frames": False,
                }
            ),
            cameras[0],
            observers[0],
            output_dir,
        )
    ]
    assert "pilot-repeat-1\tcompleted" in stdout.getvalue()


def test_list_command_never_constructs_capture_resources() -> None:
    def forbidden_factory(**_kwargs: object) -> object:
        raise AssertionError("list must not construct capture resources")

    exit_code = main(
        ["list"],
        stdout=io.StringIO(),
        enumerate_cameras=lambda: [],
        camera_factory=forbidden_factory,
        observer_factory=forbidden_factory,
    )

    assert exit_code == 0


def test_capture_command_closes_resources_when_run_store_fails(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "capture.yaml"
    model_path = tmp_path / "face_landmarker.task"
    _write_capture_config(config_path)
    model_path.write_bytes(b"model")
    camera = FakeCamera()
    observer = FakeObserver()

    def fail_capture(*_args: object) -> object:
        raise RuntimeError("capture failed")

    with pytest.raises(RuntimeError, match="capture failed"):
        main(
            [
                "capture",
                str(config_path),
                str(tmp_path / "run"),
                "--model-path",
                str(model_path),
            ],
            stdout=io.StringIO(),
            camera_factory=lambda **_kwargs: camera,
            observer_factory=lambda **_kwargs: observer,
            capture_runner=fail_capture,
        )

    assert camera.closed is True
    assert observer.closed is True
