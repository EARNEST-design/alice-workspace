"""CLI entry points for passive camera discovery."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Callable, TextIO

from alice.perception.camera import CameraInfo, list_cameras


def _write_camera_table(stdout: TextIO, cameras: list[CameraInfo]) -> None:
    stdout.write("camera_id\tdevice\tlabel\tcapabilities\n")
    for camera in cameras:
        stdout.write(
            f"{camera.camera_id}\t{camera.device}\t{camera.label}\t"
            f"{','.join(camera.capabilities)}\n"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alice-camera")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    enumerate_cameras: Callable[[], list[CameraInfo]] = list_cameras,
) -> int:
    output = stdout or sys.stdout
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "list":
        _write_camera_table(output, list(enumerate_cameras()))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
