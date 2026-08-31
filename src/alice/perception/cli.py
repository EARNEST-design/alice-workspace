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
from alice.perception.camera import CameraInfo, OpenCVCamera, list_cameras
from alice.perception.mediapipe_adapter import MediaPipeBlendshapeAdapter


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


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    enumerate_cameras: Callable[[], list[CameraInfo]] = list_cameras,
    camera_factory: Callable[..., Any] = OpenCVCamera,
    observer_factory: Callable[..., Any] = MediaPipeBlendshapeAdapter,
    capture_runner: Callable[..., ArtifactManifest] = run_passive_capture,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "list":
        _write_camera_table(output, list(enumerate_cameras()))
        return 0
    if args.command == "capture":
        config = _load_capture_config(args.config_path, run_id=args.run_id)
        if config.camera_device is None:
            raise ValueError("camera_device is required for capture")
        camera = camera_factory(
            camera_id=config.camera_id,
            device=config.camera_device,
            width=config.requested_width,
            height=config.requested_height,
            fps=config.requested_fps,
        )
        observer = observer_factory(
            camera_id=config.camera_id,
            model_path=args.model_path,
        )
        try:
            camera.open()
            manifest = capture_runner(config, camera, observer, args.output_dir)
        finally:
            camera.close()
            observer.close()
        output.write(f"{manifest.run_id}\t{manifest.status}\t{args.output_dir}\n")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
