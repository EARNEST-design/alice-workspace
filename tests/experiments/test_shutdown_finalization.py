from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import alice.experiments.hardware_identification as module
from alice.experiments.hardware_identification import (
    FailedRunPowerRemovalConfirmation,
    PowerEnableChallenge,
    record_failed_hardware_power_removal,
    verify_shutdown_finalization,
)


def test_shutdown_finalization_is_immutable_and_detects_linked_mutation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "aborted-run"
    output.mkdir()
    manifest = output / "manifest.json"
    manifest.write_text('{"run_id":"run","status":"aborted"}')
    original = {path.name: path.read_bytes() for path in output.iterdir()}
    confirmation = FailedRunPowerRemovalConfirmation(
        run_id="run",
        challenge_id="challenge",
        config_sha256="a" * 64,
        manifest_sha256="b" * 64,
        output_identity_sha256="c" * 64,
        confirmed_at=datetime.now(UTC),
        confirmed_monotonic_ns=1,
        source="test operator",
        operator_acknowledgment="I CONFIRM MASTER SERVO POWER IS OFF",
    )

    generation = record_failed_hardware_power_removal(
        output_dir=output, confirmation=confirmation
    )

    verified = verify_shutdown_finalization(generation, aborted_output_dir=output)
    assert verified.outcome == "power_removed_confirmed"
    assert {path.name: path.read_bytes() for path in output.iterdir()} == original
    assert not (output / "aborted-shutdown.json").exists()
    evidence = generation / "shutdown-evidence.json"
    evidence.write_text(json.dumps({"tampered": True}))
    with pytest.raises(ValueError, match="shutdown evidence"):
        verify_shutdown_finalization(generation, aborted_output_dir=output)
    evidence.unlink()
    with pytest.raises(FileNotFoundError):
        verify_shutdown_finalization(generation, aborted_output_dir=output)


def test_shutdown_finalization_detects_original_manifest_mutation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "aborted-run"
    output.mkdir()
    (output / "manifest.json").write_text('{"run_id":"run","status":"aborted"}')
    challenge = PowerEnableChallenge(
        challenge_id="challenge",
        run_id="run",
        config_sha256="a" * 64,
        manifest_sha256="b" * 64,
        electrical_evidence_sha256="c" * 64,
        output_identity_sha256="d" * 64,
        issued_at=datetime.now(UTC),
        issued_monotonic_ns=1,
        expires_monotonic_ns=2,
        challenge_sha256="e" * 64,
    )
    from alice.experiments.hardware_identification import (
        record_failed_hardware_power_removal_unconfirmed,
    )

    generation = record_failed_hardware_power_removal_unconfirmed(
        output_dir=output, challenge=challenge
    )
    assert (
        verify_shutdown_finalization(generation, aborted_output_dir=output).outcome
        == "power_removal_unconfirmed"
    )
    (output / "manifest.json").write_text("changed")
    with pytest.raises(ValueError, match="aborted run manifest"):
        verify_shutdown_finalization(generation, aborted_output_dir=output)


def test_shutdown_finalization_generation_is_exclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "aborted-run"
    output.mkdir()
    (output / "manifest.json").write_text('{"run_id":"run","status":"aborted"}')
    challenge = PowerEnableChallenge(
        challenge_id="challenge",
        run_id="run",
        config_sha256="a" * 64,
        manifest_sha256="b" * 64,
        electrical_evidence_sha256="c" * 64,
        output_identity_sha256="d" * 64,
        issued_at=datetime.now(UTC),
        issued_monotonic_ns=1,
        expires_monotonic_ns=2,
        challenge_sha256="e" * 64,
    )
    monkeypatch.setattr(module.secrets, "token_hex", lambda _: "fixed")
    module.record_failed_hardware_power_removal_unconfirmed(
        output_dir=output, challenge=challenge
    )
    with pytest.raises(FileExistsError, match="already exists"):
        module.record_failed_hardware_power_removal_unconfirmed(
            output_dir=output, challenge=challenge
        )
