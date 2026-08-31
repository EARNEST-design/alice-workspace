"""Interactive, checkpointed Phase 2 hardware-run composition CLI."""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, TypeVar

import yaml  # type: ignore[import-untyped]

from alice.experiments.hardware_identification import (
    HardwareApproval,
    HardwarePreflightAttestation,
    PowerEnableConfirmation,
    PowerRemovalConfirmation,
    execute_prepared_hardware_identification,
    finalize_hardware_identification,
    load_hardware_identification_config,
    prepare_hardware_identification,
)

_prepare = prepare_hardware_identification
_execute = execute_prepared_hardware_identification
_finalize = finalize_hardware_identification
_input = input
_POWER_ON: Final[
    Literal["I CONFIRM MASTER SERVO POWER IS ON AND POWER REMOVAL IS READY"]
] = "I CONFIRM MASTER SERVO POWER IS ON AND POWER REMOVAL IS READY"
_POWER_OFF: Final[Literal["I CONFIRM MASTER SERVO POWER IS OFF"]] = (
    "I CONFIRM MASTER SERVO POWER IS OFF"
)

_Evidence = TypeVar("_Evidence", HardwareApproval, HardwarePreflightAttestation)


def _load(path: Path, model: type[_Evidence]) -> _Evidence:
    return model.model_validate(yaml.safe_load(path.read_text()))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Guarded Phase 2 prepare, execute, power-removal, finalize flow"
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--attestation", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--enable-hardware", action="store_true")
    args = parser.parse_args(argv)
    config = load_hardware_identification_config(args.config)
    print(
        "mode=verifier-only; no device is opened unless --enable-hardware "
        "passes all gates"
    )
    if not args.enable_hardware:
        return 0
    if any(
        value.startswith("REQUIRED_")
        for value in (
            config.run_id,
            config.approval_id,
            config.enable_token.get_secret_value(),
            config.electrical_evidence_path,
            config.detector_model_path,
        )
    ):
        print("refused: repository config retains REQUIRED placeholders")
        return 2
    if args.approval is None or args.attestation is None or args.output is None:
        print("refused: approval, attestation, and output are required")
        return 2
    approval = _load(args.approval, HardwareApproval)
    attestation = _load(args.attestation, HardwarePreflightAttestation)
    prepared = _prepare(
        config_path=args.config,
        manifest_path=args.manifest,
        approval=approval,
        attestation=attestation,
        enable_hardware=True,
    )
    challenge = prepared.challenge
    print("CHECKPOINT: preparation complete with master servo power OFF")
    if _input(f"Type exactly to enable power: {_POWER_ON}\n> ") != _POWER_ON:
        print("refused: power-enable confirmation mismatch")
        return 2
    power_on = PowerEnableConfirmation(
        run_id=challenge.run_id,
        challenge_id=challenge.challenge_id,
        config_sha256=challenge.config_sha256,
        manifest_sha256=challenge.manifest_sha256,
        electrical_evidence_sha256=challenge.electrical_evidence_sha256,
        challenge_sha256=challenge.challenge_sha256,
        confirmed_at=datetime.now(UTC),
        confirmed_monotonic_ns=time.monotonic_ns(),
        source="interactive hardware-run CLI",
        operator_acknowledgment=_POWER_ON,
    )
    pending = _execute(prepared=prepared, output_dir=args.output, confirmation=power_on)
    print("CHECKPOINT: motion ended and interfaces closed; remove master servo power")
    if _input(f"Type exactly after power is OFF: {_POWER_OFF}\n> ") != _POWER_OFF:
        print("incomplete: staged evidence remains pending power-removal confirmation")
        return 3
    power_off = PowerRemovalConfirmation(
        run_id=pending.draft.run_id,
        challenge_id=pending.draft.challenge_id,
        config_sha256=pending.draft.config_sha256,
        manifest_sha256=pending.draft.manifest_sha256,
        draft_sha256=pending.draft.draft_sha256,
        confirmed_at=datetime.now(UTC),
        confirmed_monotonic_ns=time.monotonic_ns(),
        source="interactive hardware-run CLI",
        operator_acknowledgment=_POWER_OFF,
    )
    _finalize(pending=pending, confirmation=power_off)
    print(f"completed={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
