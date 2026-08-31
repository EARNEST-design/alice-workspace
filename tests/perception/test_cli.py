import builtins
import importlib
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from alice.experiments.passive_capture import PassiveCaptureConfig
from alice.perception.camera import CameraInfo
from alice.perception.cli import main

ALICE_PHASE_1_PREVIEW_DEVICE = (
    "/dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index0"
)
FUTURE_USER_CAMERA_DEVICE = (
    "/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920_BF4BEEAF-video-index0"
)


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
        self.close_count = 0
        self.close_error: RuntimeError | None = None

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class FakeObserver:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.closed = False
        self.close_count = 0
        self.close_error: RuntimeError | None = None

    def close(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error
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
maximum_observation_age_ms: 250
retain_frames: false
retention_policy:
  policy_id: derived-only
  mode: derived_observations_only
  retention_duration_days: 365
setup:
  camera_id: alice-face-webcam
  stable_camera_identity: usb-Alice-video-index0
  alice_full_face_confirmed: true
  participant_exclusion_confirmed: true
  confirmation:
    confirmed_at: 2026-08-31T09:00:00Z
    source: operator preview
  lighting: {state: confirmed, detail: lab lights}
  placement: {state: confirmed, detail: tripod}
  focus: {state: unknown, detail: not measurable}
  exposure: {state: unknown, detail: not measurable}
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
                    "maximum_observation_age_ms": 250,
                    "retain_frames": False,
                    "retention_policy": {
                        "policy_id": "derived-only",
                        "mode": "derived_observations_only",
                        "retention_duration_days": 365,
                        },
                        "setup": {
                            "camera_id": "alice-face-webcam",
                            "stable_camera_identity": "usb-Alice-video-index0",
                        "alice_full_face_confirmed": True,
                        "participant_exclusion_confirmed": True,
                        "confirmation": {
                            "confirmed_at": "2026-08-31T09:00:00Z",
                            "source": "operator preview",
                        },
                        "lighting": {"state": "confirmed", "detail": "lab lights"},
                        "placement": {"state": "confirmed", "detail": "tripod"},
                        "focus": {"state": "unknown", "detail": "not measurable"},
                        "exposure": {
                            "state": "unknown",
                            "detail": "not measurable",
                        },
                    },
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
    assert camera.close_count == 1
    assert observer.closed is True


@pytest.mark.parametrize(
    "camera_device",
    [
        "/dev/ttyACM0",
        "/tmp/random-device",
        "relative/path",
    ],
)
def test_capture_command_rejects_non_v4l2_selector_before_factory_calls(
    tmp_path: Path,
    camera_device: str,
) -> None:
    config_path = tmp_path / "capture.yaml"
    config_path.write_text(
        f"""\
run_id: pilot-template
camera_id: alice-face-webcam
camera_device: {camera_device}
requested_width: 640
requested_height: 480
requested_fps: 10
duration_seconds: 1
sample_count: 1
sample_interval_ms: 0
maximum_observation_age_ms: 250
retain_frames: false
retention_policy:
  policy_id: derived-only
  mode: derived_observations_only
  retention_duration_days: 365
setup:
  camera_id: alice-face-webcam
  stable_camera_identity: usb-Alice-video-index0
  alice_full_face_confirmed: true
  participant_exclusion_confirmed: true
  confirmation:
    confirmed_at: 2026-08-31T09:00:00Z
    source: operator preview
  lighting: {{state: confirmed, detail: lab lights}}
  placement: {{state: confirmed, detail: tripod}}
  focus: {{state: unknown, detail: not measurable}}
  exposure: {{state: unknown, detail: not measurable}}
""",
        encoding="utf-8",
    )
    model_path = tmp_path / "face_landmarker.task"
    model_path.write_bytes(b"model")

    def forbidden_factory(**_kwargs: object) -> object:
        raise AssertionError("factory must not be called")

    with pytest.raises(ValueError, match="camera_device"):
        main(
            [
                "capture",
                str(config_path),
                str(tmp_path / "run"),
                "--model-path",
                str(model_path),
            ],
            stdout=io.StringIO(),
            camera_factory=forbidden_factory,
            observer_factory=forbidden_factory,
        )


def test_capture_command_closes_camera_when_observer_factory_fails(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "capture.yaml"
    model_path = tmp_path / "face_landmarker.task"
    _write_capture_config(config_path)
    model_path.write_bytes(b"model")
    camera = FakeCamera()

    def fail_observer_factory(**_kwargs: object) -> object:
        raise RuntimeError("observer init failed")

    with pytest.raises(RuntimeError, match="observer init failed"):
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
            observer_factory=fail_observer_factory,
        )

    assert camera.closed is True


def test_capture_command_closes_observer_even_when_camera_close_fails(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "capture.yaml"
    model_path = tmp_path / "face_landmarker.task"
    _write_capture_config(config_path)
    model_path.write_bytes(b"model")
    camera = FakeCamera()
    camera.close_error = RuntimeError("camera close failed")
    observer = FakeObserver()

    with pytest.raises(RuntimeError, match="camera close failed"):
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
            capture_runner=lambda *_args: SimpleNamespace(
                run_id="passive-001",
                status="completed",
            ),
        )

    assert observer.closed is True


def test_preview_command_rejects_non_v4l2_selector_before_factory_calls(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "face_landmarker.task"
    model_path.write_bytes(b"model")

    def forbidden_factory(**_kwargs: object) -> object:
        raise AssertionError("factory must not be called")

    with pytest.raises(ValueError, match="camera_device"):
        main(
            [
                "preview",
                "/dev/ttyACM0",
                "--model-path",
                str(model_path),
            ],
            stdout=io.StringIO(),
            camera_factory=forbidden_factory,
            observer_factory=forbidden_factory,
        )


@pytest.mark.parametrize(
    "camera_device",
    [
        "/dev/video0",
        "/dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index1",
        FUTURE_USER_CAMERA_DEVICE,
    ],
)
def test_preview_command_rejects_non_phase_1_preview_selector_before_factory_calls(
    tmp_path: Path,
    camera_device: str,
) -> None:
    model_path = tmp_path / "face_landmarker.task"
    model_path.write_bytes(b"model")

    def forbidden_factory(**_kwargs: object) -> object:
        raise AssertionError("factory must not be called")

    with pytest.raises(ValueError, match="Phase 1 preview requires"):
        main(
            [
                "preview",
                camera_device,
                "--model-path",
                str(model_path),
            ],
            stdout=io.StringIO(),
            camera_factory=forbidden_factory,
            observer_factory=forbidden_factory,
        )


def test_preview_command_wires_explicit_selector_and_model_path(tmp_path: Path) -> None:
    model_path = tmp_path / "face_landmarker.task"
    model_path.write_bytes(b"model")
    cameras: list[FakeCamera] = []
    detectors: list[FakeObserver] = []
    preview_calls: list[tuple[object, object, str]] = []

    def camera_factory(**kwargs: object) -> FakeCamera:
        camera = FakeCamera(**kwargs)
        cameras.append(camera)
        return camera

    def observer_factory(**kwargs: object) -> FakeObserver:
        observer = FakeObserver(**kwargs)
        detectors.append(observer)
        return observer

    def preview_runner(camera: object, detector: object, *, window_title: str) -> None:
        preview_calls.append((camera, detector, window_title))

    exit_code = main(
        [
            "preview",
            ALICE_PHASE_1_PREVIEW_DEVICE,
            "--model-path",
            str(model_path),
            "--width",
            "640",
            "--height",
            "480",
            "--fps",
            "10",
            "--window-title",
            "Alice Preview",
        ],
        stdout=io.StringIO(),
        camera_factory=camera_factory,
        observer_factory=observer_factory,
        preview_runner=preview_runner,
    )

    assert exit_code == 0
    assert cameras[0].kwargs == {
        "camera_id": "usb-046d_HD_Webcam_C525_79C73260-video-index0",
        "device": ALICE_PHASE_1_PREVIEW_DEVICE,
        "width": 640,
        "height": 480,
        "fps": 10.0,
    }
    assert detectors[0].kwargs == {
        "model_path": model_path,
    }
    assert preview_calls == [(cameras[0], detectors[0], "Alice Preview")]


def test_preview_command_closes_camera_when_detector_factory_fails_on_default_runner(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "face_landmarker.task"
    model_path.write_bytes(b"model")
    camera = FakeCamera()

    def fail_preview_detector_factory(**_kwargs: object) -> object:
        raise RuntimeError("preview detector init failed")

    with pytest.raises(RuntimeError, match="preview detector init failed"):
        main(
            [
                "preview",
                ALICE_PHASE_1_PREVIEW_DEVICE,
                "--model-path",
                str(model_path),
            ],
            stdout=io.StringIO(),
            camera_factory=lambda **_kwargs: camera,
            preview_detector_factory=fail_preview_detector_factory,
        )

    assert camera.closed is True
    assert camera.close_count == 1


def test_preview_command_default_runner_closes_cli_owned_resources_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from alice.perception import preview as preview_module

    model_path = tmp_path / "face_landmarker.task"
    model_path.write_bytes(b"model")

    class CountingPreviewCamera(FakeCamera):
        def __init__(self) -> None:
            super().__init__()

        def read(self) -> object:
            return SimpleNamespace(bgr=np.zeros((3, 4, 3), dtype=np.uint8))

    class CountingPreviewDetector(FakeObserver):
        def __init__(self) -> None:
            super().__init__(model_path=model_path)

        def detect_preview(self, _rgb: np.ndarray) -> object:
            return SimpleNamespace(face_confidence=None, scores=(), landmarks=())

    class QuitPreviewWindow:
        def show(self, _title: str, _frame: np.ndarray) -> None:
            pass

        def wait_key(self, _delay_ms: int) -> int:
            return ord("q")

        def destroy(self, _title: str) -> None:
            pass

    camera = CountingPreviewCamera()
    detector = CountingPreviewDetector()
    monkeypatch.setattr(preview_module, "OpenCVPreviewWindow", QuitPreviewWindow)

    exit_code = main(
        [
            "preview",
            ALICE_PHASE_1_PREVIEW_DEVICE,
            "--model-path",
            str(model_path),
        ],
        stdout=io.StringIO(),
        camera_factory=lambda **_kwargs: camera,
        preview_detector_factory=lambda **_kwargs: detector,
    )

    assert exit_code == 0
    assert camera.close_count == 1
    assert detector.close_count == 1


def test_preview_command_closes_cli_owned_resources_after_custom_runner_returns(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "face_landmarker.task"
    model_path.write_bytes(b"model")
    camera = FakeCamera()
    detector = FakeObserver(model_path=model_path)
    preview_calls: list[tuple[object, object, str]] = []

    def preview_runner(
        camera_arg: object,
        detector_arg: object,
        *,
        window_title: str,
    ) -> None:
        preview_calls.append((camera_arg, detector_arg, window_title))

    exit_code = main(
        [
            "preview",
            ALICE_PHASE_1_PREVIEW_DEVICE,
            "--model-path",
            str(model_path),
            "--window-title",
            "Alice Preview",
        ],
        stdout=io.StringIO(),
        camera_factory=lambda **_kwargs: camera,
        preview_detector_factory=lambda **_kwargs: detector,
        preview_runner=preview_runner,
    )

    assert exit_code == 0
    assert preview_calls == [(camera, detector, "Alice Preview")]
    assert camera.closed is True
    assert camera.close_count == 1
    assert detector.closed is True
    assert detector.close_count == 1


def test_preview_command_preserves_runner_exception_and_closes_both_resources(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "face_landmarker.task"
    model_path.write_bytes(b"model")
    camera = FakeCamera()
    detector = FakeObserver(model_path=model_path)

    def preview_runner(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("preview failed")

    with pytest.raises(RuntimeError, match="preview failed"):
        main(
            [
                "preview",
                ALICE_PHASE_1_PREVIEW_DEVICE,
                "--model-path",
                str(model_path),
            ],
            stdout=io.StringIO(),
            camera_factory=lambda **_kwargs: camera,
            preview_detector_factory=lambda **_kwargs: detector,
            preview_runner=preview_runner,
        )

    assert camera.closed is True
    assert camera.close_count == 1
    assert detector.closed is True
    assert detector.close_count == 1


def test_preview_command_closes_detector_when_camera_close_fails(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "face_landmarker.task"
    model_path.write_bytes(b"model")
    camera = FakeCamera()
    camera.close_error = RuntimeError("camera close failed")
    detector = FakeObserver(model_path=model_path)

    def preview_runner(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("preview failed")

    with pytest.raises(RuntimeError, match="preview failed") as error_info:
        main(
            [
                "preview",
                ALICE_PHASE_1_PREVIEW_DEVICE,
                "--model-path",
                str(model_path),
            ],
            stdout=io.StringIO(),
            camera_factory=lambda **_kwargs: camera,
            preview_detector_factory=lambda **_kwargs: detector,
            preview_runner=preview_runner,
        )

    assert camera.close_count == 1
    assert detector.closed is True
    assert detector.close_count == 1
    assert "cleanup failed: camera close failed" in getattr(
        error_info.value, "__notes__", []
    )
