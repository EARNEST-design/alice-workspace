"""Non-actuating verifier for the proposed Phase 2 hardware procedure."""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path

from alice.experiments.hardware_identification import (
    expected_confirmation,
    load_hardware_identification_config,
)
from alice.hardware.manifest import load_manifest


def main(
    argv: Sequence[str] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify Phase 2 hardware files without opening serial or moving Alice"
        )
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--enable-hardware", action="store_true")
    args = parser.parse_args(argv)
    config = load_hardware_identification_config(args.config)
    manifest = load_manifest(args.manifest)
    config_hash = hashlib.sha256(args.config.read_bytes()).hexdigest()
    manifest_hash = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    phrase = expected_confirmation(config.run_id, config_hash, manifest_hash)
    print(f"config_sha256={config_hash}")
    print(f"manifest_sha256={manifest_hash}")
    print("mode=dry-run; this command has no Set Target or serial-open path")
    if not args.enable_hardware:
        return 0
    if input_fn(f"Type exactly: {phrase}\n> ") != phrase:
        print("refused: confirmation mismatch")
        return 2
    if config.approval_id.startswith(
        "REQUIRED_"
    ) or config.enable_token.get_secret_value().startswith("REQUIRED_"):
        print("refused: repository config retains REQUIRED placeholders")
        return 2
    if manifest_hash != config.hardware_manifest_sha256 or (
        manifest.canonical_sha256 != config.hardware_manifest_canonical_sha256
    ):
        print("refused: manifest hashes differ from reviewed config")
        return 2
    print("validated only; use the reviewed application composition for execution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
