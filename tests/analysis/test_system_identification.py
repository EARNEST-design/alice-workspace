from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from alice.analysis.system_identification import (
    BOOTSTRAP_SEED,
    IdentificationSample,
    MatrixCell,
    NamedMatrix,
    analyze_identification_artifacts,
    compare_repeat_run,
    estimate_local_jacobian,
    publish_identification_analysis,
)


def _samples(session: str, noise: float = 0.0) -> list[IdentificationSample]:
    slopes = {
        "mouth_open": {"jawOpen": 2.0, "mouthSmile": 0.5, "unused": 0.0},
        "smile": {"jawOpen": 0.25, "mouthSmile": 1.5, "unused": 0.0},
    }
    rows: list[IdentificationSample] = []
    index = 0
    for actuator, effects in slopes.items():
        for cycle in range(2):
            for position, phase in (
                (0.0, "home"),
                (0.1, "positive"),
                (0.0, "home"),
                (-0.1, "negative"),
                (0.0, "home"),
            ):
                index += 1
                for replicate in range(3):
                    jitter = noise * ((replicate - 1) + cycle * 0.25)
                    rows.append(
                        IdentificationSample(
                            session_id=session,
                            step_id=f"{actuator}-{cycle}-{index}",
                            sequence_index=index,
                            actuator_name=actuator,
                            normalized_position=position,
                            phase=phase,
                            blendshapes={
                                name: 0.4 + slope * position + jitter
                                for name, slope in effects.items()
                            },
                            controller_command_ns=index * 1_000_000,
                            controller_settled_ns=index * 1_000_000 + 2_000_000,
                            first_visual_sample_ns=index * 1_000_000 + 5_000_000,
                            visual_settled_ns=index * 1_000_000 + 8_000_000,
                        )
                    )
    return rows


def _cell(matrix: NamedMatrix, row: str, column: str) -> MatrixCell:
    for cell in matrix.cells:
        if cell.row == row and cell.column == column:
            return cell
    raise AssertionError((row, column))


def test_recovers_named_central_difference_and_uncertainty_deterministically() -> None:
    samples = _samples("session-a", 0.001) + _samples("session-b", 0.002)

    first = estimate_local_jacobian(samples)
    second = estimate_local_jacobian(samples)

    assert first == second
    assert first.bootstrap_seed == BOOTSTRAP_SEED == 20260831
    assert first.jacobian.row_labels == ("jawOpen", "mouthSmile", "unused")
    assert first.jacobian.column_labels == ("mouth_open", "smile")
    assert _cell(first.jacobian, "jawOpen", "mouth_open").value == pytest.approx(2.0)
    assert _cell(first.jacobian, "mouthSmile", "smile").value == pytest.approx(1.5)
    assert (
        _cell(first.jacobian, "jawOpen", "mouth_open").confidence_interval is not None
    )
    assert first.rank == 2
    assert first.singular_values[0] > first.singular_values[1] > 0
    assert first.condition_number.value is not None
    assert "unobservable blendshape dimensions: unused" in first.warnings


def test_reports_variance_snr_hysteresis_cross_effects_and_settling() -> None:
    metrics = estimate_local_jacobian(
        _samples("session-a", 0.01) + _samples("session-b", 0.02)
    )

    assert _cell(metrics.position_variance, "jawOpen", "mouth_open:+0.1").value > 0
    assert _cell(metrics.signal_to_noise, "jawOpen", "mouth_open").value > 1
    assert _cell(metrics.hysteresis, "jawOpen", "mouth_open").value > 0
    assert _cell(
        metrics.cross_effects, "mouthSmile", "mouth_open"
    ).value == pytest.approx(0.5)
    assert metrics.controller_settling_ms.value == pytest.approx(2.0)
    assert metrics.visual_settling_ms.value == pytest.approx(8.0)


def test_single_session_bootstrap_is_typed_unavailable_not_zero() -> None:
    metrics = estimate_local_jacobian(_samples("only-session"))
    slope = _cell(metrics.jacobian, "jawOpen", "mouth_open")

    assert slope.value == pytest.approx(2.0)
    assert slope.confidence_interval is None
    assert slope.uncertainty_status == "unavailable"
    assert "at least two sessions" in slope.uncertainty_reason


def test_repeat_comparison_preserves_labels_and_missing_values() -> None:
    reference = estimate_local_jacobian(_samples("a") + _samples("b"))
    repeat_samples = [
        sample.model_copy(
            update={
                "blendshapes": {
                    name: value
                    for name, value in sample.blendshapes.items()
                    if name != "unused"
                }
            }
        )
        for sample in (_samples("c") + _samples("d"))
    ]
    repeat = estimate_local_jacobian(repeat_samples)

    comparison = compare_repeat_run(reference, repeat)

    assert comparison.reference_seed == BOOTSTRAP_SEED
    assert _cell(comparison.absolute_delta, "unused", "mouth_open").value is None
    assert _cell(comparison.absolute_delta, "unused", "mouth_open").status == "missing"
    assert comparison.outcome == "inconclusive"


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    payload = b"".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for record in records
    )
    path.write_bytes(payload)


def _artifact_run(tmp_path: Path, *, status: str = "completed") -> Path:
    run = tmp_path / "run-a"
    run.mkdir()
    commands: list[dict[str, object]] = []
    observations: list[dict[str, object]] = []
    controller: list[dict[str, object]] = []
    visual: list[dict[str, object]] = []
    sequence = (
        (0.0, "home"),
        (0.1, "positive"),
        (0.0, "home"),
        (-0.1, "negative"),
        (0.0, "home"),
    )
    for index, (position, phase) in enumerate(sequence, 1):
        step = f"step-{index:04d}"
        commands.append(
            {
                "run_id": "run-a",
                "step_id": step,
                "phase": phase,
                "actuator_name": "mouth_open",
                "normalized_position": position,
                "authorization_kind": "normal",
                "monotonic_ns": index * 1_000_000,
                "request": {
                    "schema_version": "pose-request/v1",
                    "request_id": f"run-a-{step}",
                    "run_id": "run-a",
                    "hardware_id": "alice-face-v1",
                    "calibration_sha256": "e" * 64,
                    "issued_monotonic_ns": index * 1_000_000,
                    "expires_monotonic_ns": index * 1_000_000 + 100_000,
                    "targets": [
                        {
                            "actuator_name": "mouth_open",
                            "normalized_position": position,
                        }
                    ],
                },
            }
        )
        for sample_index in range(3):
            observations.append(
                {
                    "run_id": "run-a",
                    "step_id": step,
                    "sample_index": sample_index,
                    "observation": {
                        "schema_version": "blendshape-observation/v1",
                        "captured_at": "2026-08-31T00:00:00Z",
                        "observed_at": "2026-08-31T00:00:00Z",
                        "monotonic_ns": index * 1_000_000 + 5_000_000 + sample_index,
                        "camera_id": "camera",
                        "run_id": "run-a",
                        "detector": "detector",
                        "detector_model_sha256": "a" * 64,
                        "image_width": 640,
                        "image_height": 480,
                        "face_confidence": 1.0,
                        "validity": "valid",
                        "invalid_reason": None,
                        "scores": [{"name": "jawOpen", "score": 0.4 + 2 * position}],
                    },
                }
            )
        controller.append(
            {
                "run_id": "run-a",
                "step_id": step,
                "decision": {
                    "target_reached": True,
                    "requested_targets": [
                        {"actuator_name": "mouth_open", "normalized_position": position}
                    ],
                    "samples": [
                        {
                            "actuator_name": "mouth_open",
                            "observed_qus": 6000,
                            "target_qus": 6000,
                            "observed_monotonic_ns": index * 1_000_000 + 2_000_000,
                        }
                    ],
                    "decided_monotonic_ns": index * 1_000_000 + 2_000_000,
                },
            }
        )
        visual.append(
            {
                "run_id": "run-a",
                "step_id": step,
                "decision": {
                    "means": [{"name": "jawOpen", "value": 0.4 + 2 * position}],
                    "variances": [{"name": "jawOpen", "value": 0.0}],
                    "baseline_deltas": [{"name": "jawOpen", "value": 0.0}],
                    "variance_accepted": True,
                    "baseline_accepted": True,
                    "established_home_baseline": phase == "home",
                    "home_verified": True if phase == "home" else None,
                    "decided_monotonic_ns": index * 1_000_000 + 8_000_000,
                },
            }
        )
    names = {
        "commands.jsonl": commands,
        "observations.jsonl": observations,
        "controller-settling.jsonl": controller,
        "visual-settling.jsonl": visual,
    }
    artifacts: dict[str, dict[str, object]] = {}
    for name, records in names.items():
        _write_jsonl(run / name, records)
        payload = (run / name).read_bytes()
        artifacts[name] = {
            "path": name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
    config = {
        "schema_version": "identification-config/v1",
        "random_seeds": [],
        "actuator_names": ["mouth_open"],
    }
    config_sha256 = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = {
        "schema_version": "artifact-manifest/v1",
        "run_kind": "actuator_identification",
        "run_id": "run-a",
        "status": status,
        "started_at": "2026-08-31T00:00:00Z",
        "ended_at": "2026-08-31T00:01:00Z",
        "observation_count": len(observations),
        "config": config,
        "artifacts": artifacts,
        "git_revision": "1" * 40,
        "dependency_lock_path": "uv.lock",
        "dependency_lock_sha256": "b" * 64,
        "python_version": "3.13",
        "platform_system": "Linux",
        "platform_release": "test",
        "platform_machine": "x86_64",
        "camera_settings": {
            name: {"availability": "unavailable", "value": None, "set_succeeded": None}
            for name in ("width", "height", "fps", "focus", "exposure")
        },
        "aborted_reason": "aborted" if status == "aborted" else None,
        "failure": {"category": "interrupted", "error_type": "Interrupted"}
        if status == "aborted"
        else None,
        "conclusion": None,
        "identification_metadata": {
            "adapter_identity": {
                "backend": "mock",
                "mode": "simulation",
                "hardware_capable": False,
            },
            "observer": {
                "camera_id": "camera",
                "detector": "detector",
                "detector_model_sha256": "a" * 64,
                "camera_settings": {
                    name: {
                        "availability": "unavailable",
                        "value": None,
                        "set_succeeded": None,
                    }
                    for name in ("width", "height", "fps", "focus", "exposure")
                },
            },
            "expected_observer": {
                "camera_id": "camera",
                "detector": "detector",
                "detector_model_sha256": "a" * 64,
                "camera_settings": {
                    name: {
                        "availability": "unavailable",
                        "value": None,
                        "set_succeeded": None,
                    }
                    for name in ("width", "height", "fps", "focus", "exposure")
                },
            },
            "safety_limits": {
                "max_step": 0.2,
                "max_rate_per_second": 1.0,
                "max_acceleration_per_second_squared": 10.0,
                "watchdog_timeout_ns": 1000000000,
                "approval_max_age_ns": 1000000000,
                "preflight_max_age_ns": 1000000000,
                "command_max_age_ns": 100000000,
                "recovery_command_ttl_ns": 1000000000,
            },
            "preflight": None,
            "approval": None,
            "hardware_manifest_path": "hardware/test.yaml",
            "hardware_manifest_sha256": "c" * 64,
            "hardware_manifest_canonical_sha256": "d" * 64,
            "calibration_sha256": "e" * 64,
            "config_sha256": config_sha256,
        },
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run


def test_artifact_analysis_verifies_inputs_and_publishes_immutable_generation(
    tmp_path: Path,
) -> None:
    run = _artifact_run(tmp_path)
    metrics = analyze_identification_artifacts([run])
    assert _cell(metrics.jacobian, "jawOpen", "mouth_open").value == pytest.approx(2.0)

    manifest, generation = publish_identification_analysis([run], tmp_path / "report")
    assert manifest.analysis_kind == "system_identification"
    assert manifest.bootstrap_seed == BOOTSTRAP_SEED
    assert manifest.config.bootstrap_seed == BOOTSTRAP_SEED
    assert (
        manifest.config_sha256
        == hashlib.sha256(
            json.dumps(
                manifest.config.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    assert manifest.inputs
    assert manifest.artifacts["identification-metrics.json"].size_bytes > 0
    assert (generation / "actuator-identification-conclusion.md").is_file()
    with pytest.raises(FileExistsError):
        publish_identification_analysis(
            [run], tmp_path / "report", generation_id=manifest.generation_id
        )


def test_artifact_analysis_rejects_corruption_and_aborted_runs(tmp_path: Path) -> None:
    corrupt = _artifact_run(tmp_path)
    with (corrupt / "observations.jsonl").open("ab") as stream:
        stream.write(b"tampered\n")
    with pytest.raises(ValueError, match="size mismatch|checksum"):
        analyze_identification_artifacts([corrupt])

    other = tmp_path / "other"
    other.mkdir()
    aborted = _artifact_run(other, status="aborted")
    with pytest.raises(ValueError, match="completed"):
        analyze_identification_artifacts([aborted])


def test_artifact_analysis_verifies_unconsumed_artifacts_and_config_identity(
    tmp_path: Path,
) -> None:
    run = _artifact_run(tmp_path)
    extra = run / "statuses.jsonl"
    extra.write_bytes(b"{}\n")
    manifest = json.loads((run / "manifest.json").read_text())
    manifest["artifacts"]["statuses.jsonl"] = {
        "path": "statuses.jsonl",
        "sha256": hashlib.sha256(extra.read_bytes()).hexdigest(),
        "size_bytes": extra.stat().st_size,
    }
    (run / "manifest.json").write_text(json.dumps(manifest))
    extra.write_bytes(b"tampered-but-not-consumed\n")
    with pytest.raises(ValueError, match="size mismatch|checksum"):
        analyze_identification_artifacts([run])

    valid_parent = tmp_path / "valid"
    valid_parent.mkdir()
    valid = _artifact_run(valid_parent)
    manifest = json.loads((valid / "manifest.json").read_text())
    manifest["identification_metadata"]["config_sha256"] = "f" * 64
    (valid / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="config checksum"):
        analyze_identification_artifacts([valid])


def test_artifact_analysis_rejects_semantically_invalid_observations(
    tmp_path: Path,
) -> None:
    run = _artifact_run(tmp_path)
    records = [
        json.loads(line)
        for line in (run / "observations.jsonl").read_text().splitlines()
    ]
    records[0]["observation"]["scores"].append({"name": "jawOpen", "score": 0.5})
    _write_jsonl(run / "observations.jsonl", records)
    payload = (run / "observations.jsonl").read_bytes()
    manifest = json.loads((run / "manifest.json").read_text())
    manifest["artifacts"]["observations.jsonl"] = {
        "path": "observations.jsonl",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }
    (run / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="observation"):
        analyze_identification_artifacts([run])
