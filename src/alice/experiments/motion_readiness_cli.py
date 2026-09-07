"""Console entry point for the read-only streaming motion readiness gate."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alice.experiments.motion_readiness import ReadinessReport


def run_readiness(
    *,
    hardware_manifest: Path,
    model_package: Path,
    camera_device: Path,
) -> ReadinessReport:
    """Import ML-backed readiness only after argparse has handled ``--help``."""

    from alice.experiments.motion_readiness import run_readiness as run

    return run(
        hardware_manifest=hardware_manifest,
        model_package=model_package,
        camera_device=camera_device,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the read-only streaming motion readiness gate"
    )
    parser.add_argument("--hardware-manifest", type=Path, required=True)
    parser.add_argument("--model-package", type=Path, required=True)
    parser.add_argument("--camera-device", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_readiness(
        hardware_manifest=args.hardware_manifest,
        model_package=args.model_package,
        camera_device=args.camera_device,
    )
    encoded = report.model_dump_json(indent=2) + "\n"
    args.json_output.write_text(encoded, encoding="utf-8")
    print(json.dumps({"json_output": str(args.json_output), "status": report.status}))
    return 0 if report.status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
