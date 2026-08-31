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
    RepeatabilityThresholds,
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
    assert _cell(metrics.hysteresis, "jawOpen", "mouth_open").value == pytest.approx(0)
    assert _cell(
        metrics.cross_effects, "mouthSmile", "mouth_open"
    ).value == pytest.approx(0.5)
    assert metrics.controller_settling_ms.value == pytest.approx(2.0)
    assert metrics.command_to_first_visual_ms.value == pytest.approx(5.0)
    assert metrics.visual_window_settling_ms.value == pytest.approx(3.0)
    assert metrics.total_command_to_visual_settled_ms.value == pytest.approx(8.0)


def test_within_session_variance_excludes_between_session_offsets() -> None:
    first = _samples("session-a")
    second = [
        sample.model_copy(
            update={
                "blendshapes": {
                    name: value + 0.1 for name, value in sample.blendshapes.items()
                }
            }
        )
        for sample in _samples("session-b")
    ]

    metrics = estimate_local_jacobian(first + second)

    assert _cell(
        metrics.position_variance, "jawOpen", "mouth_open:+0.1"
    ).value == pytest.approx(0.0)
    assert (
        _cell(
            metrics.between_session_position_variance,
            "jawOpen",
            "mouth_open:+0.1",
        ).value
        > 0
    )
    assert _cell(metrics.signal_to_noise, "jawOpen", "mouth_open").status == "undefined"


def test_snr_uses_session_effects_and_rss_over_within_group_degrees_of_freedom() -> (
    None
):
    samples: list[IdentificationSample] = []
    for session in ("a", "b"):
        for index, (position, phase, values) in enumerate(
            (
                (0.0, "home", (0.0, 0.0)),
                (0.1, "positive", (1.0, 3.0)),
                (0.0, "home", (0.0, 0.0)),
                (-0.1, "negative", (-1.0, 1.0)),
                (0.0, "home", (0.0, 0.0)),
            ),
            1,
        ):
            for value in values:
                samples.append(
                    IdentificationSample(
                        session_id=session,
                        step_id=f"{session}-{index}",
                        sequence_index=index,
                        actuator_name="mouth_open",
                        normalized_position=position,
                        phase=phase,
                        blendshapes={"jawOpen": value},
                    )
                )

    metrics = estimate_local_jacobian(samples)

    assert metrics.snr_formula_revision == "session-effect-rss-pooled/v1"
    assert "sum(n_group - 1)" in metrics.snr_formula
    assert _cell(
        metrics.within_position_noise_sd, "jawOpen", "mouth_open"
    ).value == pytest.approx(2**0.5)
    assert _cell(
        metrics.signal_to_noise, "jawOpen", "mouth_open"
    ).value == pytest.approx(1 / (2**0.5))


def test_hysteresis_uses_absolute_direction_pairs_and_is_separate_from_home_drift() -> (
    None
):
    samples: list[IdentificationSample] = []
    for session, homes in (("a", (0.4, 0.5, 0.3)), ("b", (0.4, 0.3, 0.5))):
        sequence = (
            (0.0, "home", homes[0]),
            (0.1, "positive", 0.6),
            (0.0, "home", homes[1]),
            (-0.1, "negative", 0.2),
            (0.0, "home", homes[2]),
        )
        for index, (position, phase, value) in enumerate(sequence, 1):
            for replicate in range(2):
                samples.append(
                    IdentificationSample(
                        session_id=session,
                        step_id=f"{session}-{index}",
                        sequence_index=index,
                        actuator_name="mouth_open",
                        normalized_position=position,
                        phase=phase,
                        blendshapes={"jawOpen": value},
                    )
                )

    metrics = estimate_local_jacobian(samples)

    assert _cell(metrics.hysteresis, "jawOpen", "mouth_open").value == pytest.approx(
        0.2
    )
    assert _cell(
        metrics.return_to_home_drift, "jawOpen", "mouth_open"
    ).value == pytest.approx(0.1)


def test_single_session_bootstrap_is_typed_unavailable_not_zero() -> None:
    metrics = estimate_local_jacobian(_samples("only-session"))
    slope = _cell(metrics.jacobian, "jawOpen", "mouth_open")

    assert slope.value == pytest.approx(2.0)
    assert slope.confidence_interval is None
    assert slope.uncertainty_status == "unavailable"
    assert "at least two sessions" in slope.uncertainty_reason


def test_repeat_comparison_preserves_labels_and_missing_values() -> None:
    reference = estimate_local_jacobian(_samples("a"))
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
        for sample in _samples("c")
    ]
    repeat = estimate_local_jacobian(repeat_samples)

    comparison = compare_repeat_run(reference, repeat)

    assert comparison.reference_seed == BOOTSTRAP_SEED
    assert _cell(comparison.absolute_delta, "unused", "mouth_open").value is None
    assert _cell(comparison.absolute_delta, "unused", "mouth_open").status == "missing"
    assert comparison.outcome == "inconclusive"


def test_repeatability_thresholds_fail_pass_and_require_reviewed_configuration() -> (
    None
):
    reference = estimate_local_jacobian(_samples("a"))
    shifted = [
        sample.model_copy(
            update={
                "blendshapes": {
                    **sample.blendshapes,
                    "jawOpen": sample.blendshapes["jawOpen"]
                    + 0.4 * sample.normalized_position,
                }
            }
        )
        for sample in _samples("c")
    ]
    repeat = estimate_local_jacobian(shifted)
    strict = RepeatabilityThresholds.model_validate(
        {
            "schema_version": "identification-repeatability-thresholds/v1",
            "cells": [
                {
                    "blendshape_name": "jawOpen",
                    "actuator_name": "mouth_open",
                    "maximum_absolute_delta": 0.1,
                }
            ],
            "maximum_mean_absolute_delta": 0.25,
        }
    )
    loose = strict.model_copy(
        update={
            "cells": tuple(
                item.model_copy(update={"maximum_absolute_delta": 0.5})
                for item in strict.cells
            )
        }
    )

    assert compare_repeat_run(reference, repeat).outcome == "inconclusive"
    failed = compare_repeat_run(reference, repeat, strict)
    passed = compare_repeat_run(reference, repeat, loose)
    assert failed.outcome == "fail"
    assert any(check.passed is False for check in failed.checks)
    assert passed.outcome == "pass"
    assert all(check.passed is True for check in passed.checks)


def test_repeatability_exceeded_check_dominates_another_missing_check() -> None:
    reference = estimate_local_jacobian(_samples("a"))
    repeat_samples = [
        sample.model_copy(
            update={
                "blendshapes": {
                    "jawOpen": sample.blendshapes["jawOpen"]
                    + 0.4 * sample.normalized_position,
                    "mouthSmile": sample.blendshapes["mouthSmile"],
                }
            }
        )
        for sample in _samples("c")
    ]
    repeat = estimate_local_jacobian(repeat_samples)
    thresholds = RepeatabilityThresholds.model_validate(
        {
            "schema_version": "identification-repeatability-thresholds/v1",
            "cells": [
                {
                    "blendshape_name": "jawOpen",
                    "actuator_name": "mouth_open",
                    "maximum_absolute_delta": 0.1,
                },
                {
                    "blendshape_name": "unused",
                    "actuator_name": "mouth_open",
                    "maximum_absolute_delta": 0.1,
                },
            ],
        }
    )

    result = compare_repeat_run(reference, repeat, thresholds)

    assert any(check.passed is False for check in result.checks)
    assert any(check.passed is None for check in result.checks)
    assert result.outcome == "fail"


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    payload = b"".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for record in records
    )
    path.write_bytes(payload)


def _rewrite_artifact(run: Path, name: str, records: list[dict[str, object]]) -> None:
    _write_jsonl(run / name, records)
    payload = (run / name).read_bytes()
    manifest = json.loads((run / "manifest.json").read_text())
    manifest["artifacts"][name] = {
        "path": name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }
    (run / "manifest.json").write_text(json.dumps(manifest))


def _artifact_run(
    tmp_path: Path, *, status: str = "completed", run_id: str = "run-a"
) -> Path:
    run = tmp_path / run_id
    run.mkdir()
    commands: list[dict[str, object]] = []
    observations: list[dict[str, object]] = []
    statuses: list[dict[str, object]] = []
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
        base_ns = index * 20_000_000
        commands.append(
            {
                "run_id": run_id,
                "step_id": step,
                "phase": phase,
                "actuator_name": "mouth_open",
                "normalized_position": position,
                "authorization_kind": "normal",
                "monotonic_ns": base_ns,
                "request": {
                    "schema_version": "pose-request/v1",
                    "request_id": f"{run_id}-{step}",
                    "run_id": run_id,
                    "hardware_id": "alice-face-v1",
                    "calibration_sha256": "e" * 64,
                    "issued_monotonic_ns": base_ns,
                    "expires_monotonic_ns": base_ns + 100_000,
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
                    "run_id": run_id,
                    "step_id": step,
                    "sample_index": sample_index,
                    "observation": {
                        "schema_version": "blendshape-observation/v1",
                        "captured_at": "2026-08-31T00:00:00Z",
                        "observed_at": "2026-08-31T00:00:00Z",
                        "monotonic_ns": base_ns + 5_000_000 + sample_index,
                        "camera_id": "camera",
                        "run_id": run_id,
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
                "run_id": run_id,
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
                            "observed_monotonic_ns": base_ns + 2_000_000,
                        }
                    ],
                    "decided_monotonic_ns": base_ns + 2_000_000,
                },
            }
        )
        statuses.append(
            {
                "run_id": run_id,
                "step_id": step,
                "monotonic_ns": base_ns + 2_000_000,
                "status": {
                    "schema_version": "actuator-status/v1",
                    "request_id": f"{run_id}-{step}",
                    "run_id": run_id,
                    "hardware_id": "alice-face-v1",
                    "calibration_sha256": "e" * 64,
                    "reported_monotonic_ns": base_ns + 2_000_000,
                    "state": "applied",
                    "applied_targets": [
                        {
                            "actuator_name": "mouth_open",
                            "normalized_position": position,
                        }
                    ],
                    "fault_code": None,
                    "detail": None,
                    "controller_output_samples": [
                        {
                            "actuator_name": "mouth_open",
                            "observed_qus": 6000,
                            "target_qus": 6000,
                            "observed_monotonic_ns": base_ns + 2_000_000,
                        }
                    ],
                    "targets_reached": True,
                },
            }
        )
        visual.append(
            {
                "run_id": run_id,
                "step_id": step,
                "decision": {
                    "means": [{"name": "jawOpen", "value": 0.4 + 2 * position}],
                    "variances": [{"name": "jawOpen", "value": 0.0}],
                    "baseline_deltas": [{"name": "jawOpen", "value": 0.0}],
                    "variance_accepted": True,
                    "baseline_accepted": True,
                    "established_home_baseline": phase == "home",
                    "home_verified": True if phase == "home" else None,
                    "decided_monotonic_ns": base_ns + 8_000_000,
                },
            }
        )
    names = {
        "commands.jsonl": commands,
        "observations.jsonl": observations,
        "statuses.jsonl": statuses,
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
        "hardware_id": "alice-face-v1",
        "calibration_sha256": "e" * 64,
        "hardware_manifest_sha256": "c" * 64,
        "hardware_manifest_canonical_sha256": "d" * 64,
        "offsets": [0.1, -0.1],
        "samples_per_step": 3,
        "command_interval_ms": 250,
        "controller_settle_ms": 20,
        "visual_settle_ms": 30,
        "sample_interval_ms": 10,
        "step_timeout_ms": 2000,
        "command_ttl_ms": 100,
        "maximum_visual_variance": 0.01,
        "home_delta_tolerances": {"jawOpen": 0.01},
        "recovery_timeout_ms": 2000,
        "maximum_recovery_attempts": 20,
        "random_seeds": [],
        "actuator_names": ["mouth_open"],
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
    }
    config_sha256 = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = {
        "schema_version": "artifact-manifest/v1",
        "run_kind": "actuator_identification",
        "run_id": run_id,
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
    run = _artifact_run(tmp_path, run_id="run-a")
    repeat = _artifact_run(tmp_path, run_id="run-b")
    metrics = analyze_identification_artifacts([run])
    assert _cell(metrics.jacobian, "jawOpen", "mouth_open").value == pytest.approx(2.0)

    manifest, generation = publish_identification_analysis(
        reference_run_dir=run,
        held_out_repeat_run_dir=repeat,
        output_dir=tmp_path / "report",
    )
    assert manifest.analysis_kind == "system_identification"
    assert manifest.bootstrap_seed == BOOTSTRAP_SEED
    assert manifest.config.bootstrap_seed == BOOTSTRAP_SEED
    assert manifest.config.snr_formula_revision == "session-effect-rss-pooled/v1"
    assert "sum(n_group - 1)" in manifest.config.snr_formula
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
            reference_run_dir=run,
            held_out_repeat_run_dir=repeat,
            output_dir=tmp_path / "report",
            generation_id=manifest.generation_id,
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


def test_artifact_analysis_rejects_incompatible_sessions(tmp_path: Path) -> None:
    first = _artifact_run(tmp_path, run_id="run-a")
    second = _artifact_run(tmp_path, run_id="run-b")
    manifest = json.loads((second / "manifest.json").read_text())
    manifest["config"]["visual_settle_ms"] = 99
    manifest["identification_metadata"]["config_sha256"] = hashlib.sha256(
        json.dumps(manifest["config"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (second / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="compatibility signature"):
        analyze_identification_artifacts([first, second])


def test_artifact_analysis_refuses_pooling_different_negotiated_camera_state(
    tmp_path: Path,
) -> None:
    first = _artifact_run(tmp_path, run_id="run-a")
    second = _artifact_run(tmp_path, run_id="run-b")
    manifest = json.loads((second / "manifest.json").read_text())
    focus = {"availability": "available", "value": 22.0, "set_succeeded": True}
    manifest["config"]["observer"]["camera_settings"]["focus"] = focus
    manifest["identification_metadata"]["observer"]["camera_settings"]["focus"] = focus
    manifest["identification_metadata"]["expected_observer"]["camera_settings"][
        "focus"
    ] = focus
    manifest["camera_settings"]["focus"] = focus
    manifest["identification_metadata"]["config_sha256"] = hashlib.sha256(
        json.dumps(manifest["config"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (second / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="compatibility signature"):
        analyze_identification_artifacts([first, second])


def test_artifact_analysis_rejects_config_observer_identity_mismatch(
    tmp_path: Path,
) -> None:
    run = _artifact_run(tmp_path)
    manifest = json.loads((run / "manifest.json").read_text())
    manifest["config"]["observer"]["camera_id"] = "unexpected-camera"
    manifest["identification_metadata"]["config_sha256"] = hashlib.sha256(
        json.dumps(manifest["config"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (run / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="observer provenance mismatch"):
        analyze_identification_artifacts([run])


@pytest.mark.parametrize(
    "artifact,mutation",
    (
        ("observations.jsonl", "camera"),
        ("observations.jsonl", "order"),
        ("commands.jsonl", "request-target"),
        ("statuses.jsonl", "request-id"),
        ("visual-settling.jsonl", "not-accepted"),
        ("visual-settling.jsonl", "mean"),
    ),
)
def test_artifact_analysis_rejects_cross_artifact_contradictions(
    tmp_path: Path, artifact: str, mutation: str
) -> None:
    run = _artifact_run(tmp_path)
    records = [json.loads(line) for line in (run / artifact).read_text().splitlines()]
    if mutation == "camera":
        records[0]["observation"]["camera_id"] = "other-camera"
    elif mutation == "order":
        records[1]["observation"]["monotonic_ns"] = 1
    elif mutation == "request-target":
        records[0]["request"]["targets"][0]["normalized_position"] = 0.1
    elif mutation == "request-id":
        records[0]["status"]["request_id"] = "wrong-request"
    elif mutation == "not-accepted":
        records[0]["decision"]["variance_accepted"] = False
    else:
        records[0]["decision"]["means"][0]["value"] = 0.9
    _rewrite_artifact(run, artifact, records)

    with pytest.raises(ValueError, match="identity|order|correl|accepted|target"):
        analyze_identification_artifacts([run])


def test_publication_reads_each_input_manifest_once_and_hashes_exact_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _artifact_run(tmp_path, run_id="run-a")
    repeat = _artifact_run(tmp_path, run_id="run-b")
    manifest_path = run / "manifest.json"
    original = manifest_path.read_bytes()
    real_read_bytes = Path.read_bytes
    reads = 0

    def counted_read(path: Path) -> bytes:
        nonlocal reads
        if path.resolve() == manifest_path.resolve():
            reads += 1
            if reads > 1:
                return b'{"mutated":"between reads"}'
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read)

    manifest, _ = publish_identification_analysis(
        reference_run_dir=run,
        held_out_repeat_run_dir=repeat,
        output_dir=tmp_path / "report",
    )

    assert reads == 1
    assert (
        manifest.inputs["run-a.manifest"].sha256 == hashlib.sha256(original).hexdigest()
    )


def test_publication_records_repeatability_thresholds_hash_and_outcome(
    tmp_path: Path,
) -> None:
    first = _artifact_run(tmp_path, run_id="run-a")
    second = _artifact_run(tmp_path, run_id="run-b")
    thresholds = RepeatabilityThresholds.model_validate(
        {
            "schema_version": "identification-repeatability-thresholds/v1",
            "cells": [
                {
                    "blendshape_name": "jawOpen",
                    "actuator_name": "mouth_open",
                    "maximum_absolute_delta": 0.01,
                }
            ],
        }
    )

    manifest, generation = publish_identification_analysis(
        reference_run_dir=first,
        held_out_repeat_run_dir=second,
        output_dir=tmp_path / "report",
        repeatability_thresholds=thresholds,
    )

    assert manifest.repeatability_thresholds == thresholds
    assert (
        manifest.repeatability_thresholds_sha256
        == hashlib.sha256(
            json.dumps(
                thresholds.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    assert manifest.conclusion.outcome == "pass"
    repeatability = json.loads((generation / "repeatability.json").read_text())
    assert repeatability["outcome"] == "pass"
    assert repeatability["reference_run_id"] == "run-a"
    assert repeatability["repeat_run_id"] == "run-b"
    assert manifest.reference_run_id == "run-a"
    assert manifest.held_out_repeat_run_id == "run-b"
    assert manifest.conclusion.reference_run_id == "run-a"
    assert manifest.conclusion.held_out_repeat_run_id == "run-b"


def test_publication_roles_are_explicit_not_inferred_from_caller_order(
    tmp_path: Path,
) -> None:
    first = _artifact_run(tmp_path, run_id="run-a")
    second = _artifact_run(tmp_path, run_id="run-b")

    manifest, generation = publish_identification_analysis(
        reference_run_dir=second,
        held_out_repeat_run_dir=first,
        output_dir=tmp_path / "report",
    )

    assert manifest.reference_run_id == "run-b"
    assert manifest.held_out_repeat_run_id == "run-a"
    repeatability = json.loads((generation / "repeatability.json").read_text())
    assert repeatability["group_assignments"] == [
        {"role": "reference", "run_id": "run-b"},
        {"role": "held_out_repeat", "run_id": "run-a"},
    ]
    with pytest.raises(ValueError, match="distinct"):
        publish_identification_analysis(
            reference_run_dir=first,
            held_out_repeat_run_dir=first,
            output_dir=tmp_path / "bad-report",
        )
