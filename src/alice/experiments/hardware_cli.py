"""Non-actuating verifier for the proposed Phase 2 hardware procedure."""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from alice.experiments.hardware_identification import (
    ElectricalSafetyEvidence,
    HardwareIdentificationConfig,
    expected_confirmation,
)
from alice.hardware.manifest import HardwareManifest


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
    config_bytes = args.config.read_bytes()
    manifest_bytes = args.manifest.read_bytes()
    config = HardwareIdentificationConfig.model_validate(yaml.safe_load(config_bytes))
    manifest = HardwareManifest.model_validate(yaml.safe_load(manifest_bytes))
    config_hash = hashlib.sha256(config_bytes).hexdigest()
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    evidence_hash: str | None = None
    evidence_valid = False
    if config.electrical_evidence_path.startswith("REQUIRED_"):
        print("electrical_evidence=unresolved")
    else:
        evidence_bytes = Path(config.electrical_evidence_path).read_bytes()
        ElectricalSafetyEvidence.model_validate(yaml.safe_load(evidence_bytes))
        evidence_hash = hashlib.sha256(evidence_bytes).hexdigest()
        evidence_valid = evidence_hash == config.electrical_evidence_sha256
        print(f"electrical_evidence_sha256={evidence_hash}")
    phrase = expected_confirmation(
        config.run_id, config_hash, manifest_hash, evidence_hash
    )
    print(f"config_sha256={config_hash}")
    print(f"manifest_sha256={manifest_hash}")
    print("mode=dry-run; this command has no Set Target or serial-open path")
    if not args.enable_hardware:
        return 0
    if input_fn(f"Type exactly: {phrase}\n> ") != phrase:
        print("refused: confirmation mismatch")
        return 2
    if (
        config.approval_id.startswith("REQUIRED_")
        or config.enable_token.get_secret_value().startswith("REQUIRED_")
        or not evidence_valid
    ):
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
