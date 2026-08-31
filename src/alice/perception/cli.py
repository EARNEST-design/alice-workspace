"""CLI entry points for passive camera discovery and explicit capture."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable, TextIO

import yaml  # type: ignore[import-untyped]

from alice.experiments.manifest import ArtifactManifest
from alice.experiments.passive_capture import (
    PassiveCaptureConfig,
    run_passive_capture,
)
from alice.perception.camera import (
    CameraInfo,
    OpenCVCamera,
    list_cameras,
    validate_camera_device_selector,
)
from alice.perception.mediapipe_adapter import (
    MediaPipeBlendshapeAdapter,
    MediaPipeTaskDetector,
)
from alice.perception.preview import run_preview

PHASE_1_PREVIEW_CAMERA_DEVICE = (
    "/dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index0"
)


def _write_camera_table(stdout: TextIO, cameras: list[CameraInfo]) -> None:
    stdout.write("camera_id\tdevice\tlabel\tcapabilities\tcapability_error\n")
    for camera in cameras:
        stdout.write(
            f"{camera.camera_id}\t{camera.device}\t{camera.label}\t"
            f"{','.join(camera.capabilities)}\t{camera.capability_error or ''}\n"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alice-camera")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    preview_parser = subparsers.add_parser("preview")
    preview_parser.add_argument("camera_device", type=str)
    preview_parser.add_argument("--model-path", type=Path, required=True)
    preview_parser.add_argument("--width", type=int, default=640)
    preview_parser.add_argument("--height", type=int, default=480)
    preview_parser.add_argument("--fps", type=float, default=10.0)
    preview_parser.add_argument(
        "--window-title",
        default="alice-camera preview",
    )
    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("config_path", type=Path)
    capture_parser.add_argument("output_dir", type=Path)
    capture_parser.add_argument("--model-path", type=Path, required=True)
    capture_parser.add_argument("--run-id")
    return parser


def _load_capture_config(
    config_path: Path,
    *,
    run_id: str | None,
) -> PassiveCaptureConfig:
    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError("capture config must contain a YAML mapping")
    if run_id is not None:
        payload["run_id"] = run_id
    return PassiveCaptureConfig.model_validate(payload)


def _close_owned_resources(*resources: Any) -> BaseException | None:
    first_error: BaseException | None = None
    for resource in resources:
        if resource is None:
            continue
        close = getattr(resource, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except BaseException as error:  # pragma: no cover - exercised by tests
            if first_error is None:
                first_error = error
    return first_error


def _default_preview_detector_factory(*, model_path: Path) -> MediaPipeTaskDetector:
    return MediaPipeTaskDetector.from_model_path(model_path)


def _validate_phase_1_preview_camera_device(device: str) -> str:
    validated_device = validate_camera_device_selector(device)
    if validated_device != PHASE_1_PREVIEW_CAMERA_DEVICE:
        raise ValueError(
            "Phase 1 preview requires "
            f"{PHASE_1_PREVIEW_CAMERA_DEVICE} as the Alice-facing C525 selector"
        )
    return validated_device


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    enumerate_cameras: Callable[[], list[CameraInfo]] = list_cameras,
    camera_factory: Callable[..., Any] = OpenCVCamera,
    observer_factory: Callable[..., Any] = MediaPipeBlendshapeAdapter,
    capture_runner: Callable[..., ArtifactManifest] = run_passive_capture,
    preview_runner: Callable[..., None] = run_preview,
    preview_detector_factory: Callable[..., Any] | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "list":
        _write_camera_table(output, list(enumerate_cameras()))
        return 0
    if args.command == "preview":
        camera_device = _validate_phase_1_preview_camera_device(args.camera_device)
        preview_factory = preview_detector_factory
        if (
            preview_factory is None
            and observer_factory is not MediaPipeBlendshapeAdapter
        ):
            preview_factory = observer_factory
        if preview_factory is None:
            preview_factory = _default_preview_detector_factory

        preview_camera: Any | None = None
        preview_detector: Any | None = None
        preview_error: BaseException | None = None
        try:
            preview_camera = camera_factory(
                camera_id=Path(camera_device).name,
                device=camera_device,
                width=args.width,
                height=args.height,
                fps=args.fps,
            )
            preview_detector = preview_factory(model_path=args.model_path)
            preview_runner(
                preview_camera,
                preview_detector,
                window_title=args.window_title,
            )
        except BaseException as error:
            preview_error = error
        close_error = _close_owned_resources(preview_camera, preview_detector)
        if preview_error is not None:
            if close_error is not None:
                preview_error.add_note(f"cleanup failed: {close_error}")
            raise preview_error
        if close_error is not None:
            raise close_error
        return 0
    if args.command == "capture":
        config = _load_capture_config(args.config_path, run_id=args.run_id)
        if config.camera_device is None:
            raise ValueError("camera_device is required for capture")
        camera_device = validate_camera_device_selector(config.camera_device)
        capture_camera: Any | None = None
        observer: Any | None = None
        capture_error: BaseException | None = None
        try:
            capture_camera = camera_factory(
                camera_id=config.camera_id,
                device=camera_device,
                width=config.requested_width,
                height=config.requested_height,
                fps=config.requested_fps,
            )
            observer = observer_factory(
                camera_id=config.camera_id,
                model_path=args.model_path,
            )
            capture_camera.open()
            manifest = capture_runner(
                config,
                capture_camera,
                observer,
                args.output_dir,
            )
        except BaseException as error:
            capture_error = error
        close_error = _close_owned_resources(capture_camera, observer)
        if capture_error is not None:
            if close_error is not None:
                capture_error.add_note(f"cleanup failed: {close_error}")
            raise capture_error
        if close_error is not None:
            raise close_error
        output.write(f"{manifest.run_id}\t{manifest.status}\t{args.output_dir}\n")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
