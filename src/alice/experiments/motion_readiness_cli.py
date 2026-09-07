"""Console entry point for the read-only streaming motion readiness gate."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alice.experiments.motion_readiness import ReadinessExecution


def execute_readiness(
    *,
    hardware_manifest: Path,
    device_config: Path,
    model_package: Path,
    camera_device: Path,
) -> ReadinessExecution:
    """Import ML-backed readiness only after argparse has handled ``--help``."""

    from alice.experiments.motion_readiness import execute_readiness as execute

    return execute(
        hardware_manifest=hardware_manifest,
        device_config=device_config,
        model_package=model_package,
        camera_device=camera_device,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the read-only streaming motion readiness gate"
    )
    parser.add_argument("--hardware-manifest", type=Path, required=True)
    parser.add_argument("--device-config", type=Path, required=True)
    parser.add_argument("--model-package", type=Path, required=True)
    parser.add_argument("--camera-device", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    args = parser.parse_args(argv)
    execution = execute_readiness(
        hardware_manifest=args.hardware_manifest,
        device_config=args.device_config,
        model_package=args.model_package,
        camera_device=args.camera_device,
    )
    from alice.experiments.motion_readiness import (
        OutputPublicationError,
        publish_readiness_report,
    )

    try:
        publish_readiness_report(
            args.json_output,
            execution.report,
            protected_file_identities=execution.protected_file_identities,
            protected_directory_identities=execution.protected_directory_identities,
        )
    except OutputPublicationError as error:
        print(json.dumps({"error_code": error.code, "status": "fail"}))
        return 2
    print(json.dumps({"status": execution.report.status}))
    return 0 if execution.report.status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
