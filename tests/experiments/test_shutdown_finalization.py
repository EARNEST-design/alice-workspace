from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from alice.experiments.hardware_identification import (
    FailedRunPowerRemovalConfirmation,
    ShutdownFinalizationManifest,
    record_failed_hardware_power_removal,
)
from alice.experiments.manifest import ArtifactRecord


def test_shutdown_finalization_rejects_nonhardware_abort(tmp_path: Path) -> None:
    output = tmp_path / "passive-abort"
    output.mkdir()
    (output / "manifest.json").write_text(
        """{
          "schema_version":"artifact-manifest/v1", "run_id":"run",
          "status":"aborted", "started_at":"2026-09-01T00:00:00Z",
          "ended_at":"2026-09-01T00:00:01Z", "observation_count":0,
          "config":{}, "artifacts":{}, "git_revision":null,
          "dependency_lock_path":null, "dependency_lock_sha256":null,
          "python_version":"3", "platform_system":"test",
          "platform_release":"test", "platform_machine":"test",
          "aborted_reason":"test",
          "failure":{"category":"interrupted","error_type":"Test"}
        }"""
    )
    confirmation = FailedRunPowerRemovalConfirmation(
        run_id="run",
        challenge_id="challenge",
        config_sha256="a" * 64,
        manifest_sha256="b" * 64,
        output_identity_sha256="c" * 64,
        confirmed_at=datetime.now(UTC),
        confirmed_monotonic_ns=1,
        source="test",
        operator_acknowledgment="I CONFIRM MASTER SERVO POWER IS OFF",
    )
    with pytest.raises(ValueError, match="aborted hardware run"):
        record_failed_hardware_power_removal(
            output_dir=output, confirmation=confirmation
        )


def test_shutdown_finalization_manifest_rejects_unsafe_logical_paths() -> None:
    record = ArtifactRecord(path="manifest.json", sha256="a" * 64, size_bytes=1)
    with pytest.raises(ValidationError, match="manifest path"):
        ShutdownFinalizationManifest(
            schema_version="shutdown-finalization-manifest/v1",
            generation_id="generation",
            run_id="run",
            outcome="power_removal_unconfirmed",
            generated_at=datetime.now(UTC),
            generated_monotonic_ns=1,
            challenge_id="challenge",
            config_sha256="b" * 64,
            execution_config_sha256="c" * 64,
            manifest_sha256="d" * 64,
            output_identity_sha256="e" * 64,
            aborted_run_manifest=record.model_copy(update={"path": "../manifest.json"}),
            shutdown_evidence=record.model_copy(
                update={"path": "shutdown-evidence.json"}
            ),
        )
