import io
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from alice.analysis.blendshape_stability import analyze_observations, phase_1_acceptance
from alice.contracts import BlendshapeObservation, BlendshapeScore, ObservationValidity
from alice.experiments.passive_capture import main as passive_capture_main


def _observation(
    *,
    frame_index: int,
    validity: ObservationValidity,
    scores: tuple[tuple[str, float], ...],
) -> BlendshapeObservation:
    return BlendshapeObservation(
        schema_version="blendshape-observation/v1",
        captured_at=datetime(2026, 8, 31, 9, 0, 0, tzinfo=UTC)
        + timedelta(milliseconds=100 * frame_index),
        observed_at=datetime(2026, 8, 31, 9, 0, 0, tzinfo=UTC)
        + timedelta(milliseconds=100 * frame_index + 5),
        monotonic_ns=1_000_000 + frame_index,
        camera_id="alice-face-webcam",
        run_id="passive-001",
        detector="mediapipe-face-landmarker",
        detector_model_sha256="a" * 64,
        image_width=640,
        image_height=480,
        face_confidence=0.92 if validity is ObservationValidity.VALID else None,
        validity=validity,
        invalid_reason=(
            None if validity is ObservationValidity.VALID else "no face seen"
        ),
        scores=tuple(BlendshapeScore(name=name, score=score) for name, score in scores),
    )


def _artifact_record_payload(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "path": path.name,
        "sha256": sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _write_manifest(
    path: Path,
    *,
    status: str = "completed",
    artifacts: dict[str, dict[str, object]] | None = None,
    conclusion: str | None = None,
    acceptance_thresholds: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        "schema_version": "artifact-manifest/v1",
        "run_id": "passive-001",
        "status": status,
        "started_at": "2026-08-31T09:00:00Z",
        "ended_at": "2026-08-31T09:02:00Z",
        "observation_count": 4,
        "config": {
            "run_id": "passive-001",
            "camera_id": "alice-face-webcam",
            "requested_width": 640,
            "requested_height": 480,
            "requested_fps": 10,
            "duration_seconds": 120,
            "sample_count": 4,
            "sample_interval_ms": 100,
            "maximum_observation_age_ms": 250,
            "retain_frames": False,
            "retention_policy": {
                "policy_id": "derived-only",
                "mode": "derived_observations_only",
                "retention_duration_days": 365,
            },
            "setup": {
                "camera_id": "alice-face-webcam",
                "stable_camera_identity": "usb-Alice-video-index0",
                "alice_full_face_confirmed": True,
                "participant_exclusion_confirmed": True,
                "confirmation": {
                    "confirmed_at": "2026-08-31T09:00:00Z",
                    "source": "operator preview",
                },
                "placement": {
                    "state": "confirmed",
                    "detail": "Tripod at eye level, 45 cm from Alice.",
                },
                "lighting": {
                    "state": "confirmed",
                    "detail": "Overhead lab lights only.",
                },
                "focus": {"state": "unknown", "detail": "not measurable"},
                "exposure": {"state": "unknown", "detail": "not measurable"},
            },
            "acceptance_thresholds": acceptance_thresholds,
        },
        "artifacts": artifacts or {},
        "git_revision": "b" * 40,
        "dependency_lock_path": "uv.lock",
        "dependency_lock_sha256": "c" * 64,
        "python_version": "3.13.7",
        "platform_system": "Linux",
        "platform_release": "6.8.0",
        "platform_machine": "x86_64",
        "camera_settings": {
            name: {
                "availability": "unavailable",
                "value": None,
                "set_succeeded": None,
            }
            for name in ("width", "height", "fps", "focus", "exposure")
        },
        "aborted_reason": None if status == "completed" else "capture aborted",
        "failure": (
            None
            if status == "completed"
            else {"category": "interrupted", "error_type": "KeyboardInterrupt"}
        ),
        "conclusion": conclusion,
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_analyze_accepts_legacy_v1_manifest_and_observations(
    tmp_path: Path,
) -> None:
    observations = [
        _observation(
            frame_index=index,
            validity=ObservationValidity.VALID,
            scores=(("jawOpen", 0.2 + index * 0.01),),
        ).model_copy(update={"observed_at": None})
        for index in range(2)
    ]
    observations_bytes = (
        "\n".join(
            json.dumps(
                {
                    key: value
                    for key, value in observation.model_dump(mode="json").items()
                    if key != "observed_at"
                },
                sort_keys=True,
            )
            for observation in observations
        )
        + "\n"
    ).encode()
    (tmp_path / "observations.jsonl").write_bytes(observations_bytes)
    _write_manifest(
        tmp_path / "manifest.json",
        artifacts={
            "observations.jsonl": _artifact_record_payload(
                tmp_path / "observations.jsonl"
            )
        },
    )
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    payload.pop("camera_settings")
    payload["config"].pop("maximum_observation_age_ms")
    payload["observation_count"] = 2
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    from alice.analysis.blendshape_stability import analyze_stability

    metrics = analyze_stability(tmp_path)

    assert metrics.total_frames == 2


@pytest.fixture
def valid_observations() -> list[BlendshapeObservation]:
    return [
        _observation(
            frame_index=0,
            validity=ObservationValidity.VALID,
            scores=(("jawOpen", 0.2), ("mouthSmile", 0.1)),
        ),
        _observation(
            frame_index=1,
            validity=ObservationValidity.VALID,
            scores=(("jawOpen", 0.2), ("mouthSmile", 0.3)),
        ),
        _observation(
            frame_index=2,
            validity=ObservationValidity.NO_FACE,
            scores=(),
        ),
        _observation(
            frame_index=3,
            validity=ObservationValidity.VALID,
            scores=(("jawOpen", 0.2), ("mouthSmile", 0.5)),
        ),
    ]


def test_stability_reports_detection_and_per_category_variance(
    valid_observations: list[BlendshapeObservation],
) -> None:
    metrics = analyze_observations(valid_observations)

    assert metrics.total_frames == 4
    assert metrics.valid_frames == 3
    assert metrics.detection_rate == pytest.approx(0.75)
    assert tuple(metrics.category_names) == ("jawOpen", "mouthSmile")

    jaw_open = metrics.categories["jawOpen"]
    assert jaw_open.count == 3
    assert jaw_open.mean == pytest.approx(0.2)
    assert jaw_open.standard_deviation == pytest.approx(0.0)
    assert jaw_open.median == pytest.approx(0.2)
    assert jaw_open.percentile_05 == pytest.approx(0.2)
    assert jaw_open.percentile_95 == pytest.approx(0.2)
    assert jaw_open.percentile_range == pytest.approx(0.0)
    assert jaw_open.warmup_drift == pytest.approx(0.0)
    assert jaw_open.lag1_autocorrelation is None

    mouth_smile = metrics.categories["mouthSmile"]
    assert mouth_smile.mean == pytest.approx(0.3)
    assert mouth_smile.standard_deviation == pytest.approx(0.16329931618554522)
    assert mouth_smile.median == pytest.approx(0.3)
    assert mouth_smile.percentile_05 == pytest.approx(0.12)
    assert mouth_smile.percentile_95 == pytest.approx(0.48)
    assert mouth_smile.percentile_range == pytest.approx(0.36)
    assert mouth_smile.warmup_drift == pytest.approx(0.3)
    assert mouth_smile.lag1_autocorrelation == pytest.approx(1.0)
    assert metrics.correlation_matrix == {
        "jawOpen": {"jawOpen": None, "mouthSmile": None},
        "mouthSmile": {"jawOpen": None, "mouthSmile": pytest.approx(1.0)},
    }
    assert metrics.effective_dimensionality == pytest.approx(1.0)


@pytest.mark.parametrize(
    "scores",
    [
        (("jawOpen", 0.2),),
        (("mouthSmile", 0.3), ("jawOpen", 0.2)),
    ],
)
def test_stability_rejects_missing_or_reordered_categories(
    valid_observations: list[BlendshapeObservation],
    scores: tuple[tuple[str, float], ...],
) -> None:
    observations = valid_observations[:2] + [
        _observation(
            frame_index=2,
            validity=ObservationValidity.VALID,
            scores=scores,
        )
    ]

    with pytest.raises(ValueError, match="category schema"):
        analyze_observations(observations)


def test_stability_handles_no_valid_frames_and_short_series() -> None:
    no_face_only = [
        _observation(
            frame_index=0,
            validity=ObservationValidity.NO_FACE,
            scores=(),
        ),
        _observation(
            frame_index=1,
            validity=ObservationValidity.NO_FACE,
            scores=(),
        ),
    ]

    no_face_metrics = analyze_observations(no_face_only)
    assert no_face_metrics.total_frames == 2
    assert no_face_metrics.valid_frames == 0
    assert no_face_metrics.detection_rate == pytest.approx(0.0)
    assert no_face_metrics.categories == {}

    one_valid = [
        _observation(
            frame_index=0,
            validity=ObservationValidity.VALID,
            scores=(("jawOpen", 0.4),),
        )
    ]

    short_metrics = analyze_observations(one_valid)
    jaw_open = short_metrics.categories["jawOpen"]
    assert jaw_open.count == 1
    assert jaw_open.standard_deviation == pytest.approx(0.0)
    assert jaw_open.percentile_05 == pytest.approx(0.4)
    assert jaw_open.percentile_95 == pytest.approx(0.4)
    assert jaw_open.warmup_drift is None
    assert jaw_open.lag1_autocorrelation is None


def test_phase_1_acceptance_lists_every_failed_threshold(
    valid_observations: list[BlendshapeObservation],
) -> None:
    metrics = analyze_observations(valid_observations)

    result = phase_1_acceptance(
        metrics,
        {
            "minimum_detection_rate": 0.9,
            "categories": {
                "jawOpen": {"maximum_standard_deviation": 0.01},
                "mouthSmile": {
                    "maximum_percentile_range": 0.2,
                    "maximum_warmup_drift": 0.2,
                },
            },
        },
    )

    assert result.outcome == "fail"
    assert [failure.metric_path for failure in result.failed_thresholds] == [
        "detection_rate",
        "mouthSmile.percentile_range",
        "mouthSmile.warmup_drift",
    ]


def test_phase_1_acceptance_is_inconclusive_without_thresholds() -> None:
    metrics = analyze_observations(
        [
            _observation(
                frame_index=0,
                validity=ObservationValidity.NO_FACE,
                scores=(),
            )
        ]
    )

    result = phase_1_acceptance(metrics, None)

    assert result.outcome == "inconclusive"
    assert result.failed_thresholds == ()


def test_phase_1_acceptance_is_inconclusive_for_no_face_with_category_thresholds() -> (
    None
):
    metrics = analyze_observations(
        [
            _observation(
                frame_index=0,
                validity=ObservationValidity.NO_FACE,
                scores=(),
            ),
            _observation(
                frame_index=1,
                validity=ObservationValidity.NO_FACE,
                scores=(),
            ),
        ]
    )

    result = phase_1_acceptance(
        metrics,
        {
            "minimum_detection_rate": 0.8,
            "categories": {
                "jawOpen": {"maximum_standard_deviation": 0.01},
            },
        },
    )

    assert result.outcome == "fail"
    assert [check.status for check in result.checks] == [
        "fail",
        "inconclusive",
    ]
    assert (
        "no valid frames available for stability analysis"
        in result.inconclusive_reasons
    )


@pytest.mark.parametrize(
    ("captured_indexes", "monotonic_values", "message"),
    [
        ([0, 0], [10, 11], "captured_at"),
        ([0, 1], [10, 10], "monotonic_ns"),
        ([1, 0], [10, 11], "captured_at"),
        ([0, 1], [11, 10], "monotonic_ns"),
    ],
)
def test_analysis_rejects_duplicate_or_decreasing_timestamps(
    captured_indexes: list[int],
    monotonic_values: list[int],
    message: str,
) -> None:
    observations = [
        _observation(
            frame_index=frame_index,
            validity=ObservationValidity.VALID,
            scores=(("jawOpen", 0.2),),
        ).model_copy(update={"monotonic_ns": monotonic})
        for frame_index, monotonic in zip(
            captured_indexes,
            monotonic_values,
            strict=True,
        )
    ]

    with pytest.raises(ValueError, match=message):
        analyze_observations(observations)


def test_analysis_rejects_observation_older_than_manifest_limit(tmp_path: Path) -> None:
    observation = _observation(
        frame_index=0,
        validity=ObservationValidity.VALID,
        scores=(("jawOpen", 0.2),),
    ).model_copy(
        update={
            "observed_at": datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
            + timedelta(milliseconds=251)
        }
    )
    observations_path = tmp_path / "observations.jsonl"
    observations_path.write_text(
        json.dumps(observation.model_dump(mode="json"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_manifest(
        tmp_path / "manifest.json",
        artifacts={"observations.jsonl": _artifact_record_payload(observations_path)},
    )
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    payload["observation_count"] = 1
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    from alice.analysis.blendshape_stability import analyze_stability

    with pytest.raises(ValueError, match="maximum_observation_age_ms"):
        analyze_stability(tmp_path)


def test_acceptance_uses_maximum_absolute_lag1_autocorrelation() -> None:
    observations = [
        _observation(
            frame_index=index,
            validity=ObservationValidity.VALID,
            scores=(("jawOpen", score),),
        )
        for index, score in enumerate((0.0, 1.0, 0.0, 1.0))
    ]
    metrics = analyze_observations(observations)

    result = phase_1_acceptance(
        metrics,
        {"categories": {"jawOpen": {"maximum_absolute_lag1_autocorrelation": 0.5}}},
    )

    assert result.outcome == "fail"
    assert result.failed_thresholds[0].comparator == "abs<="
    assert result.failed_thresholds[0].observed == pytest.approx(-1.0)


def test_effective_dimensionality_is_two_for_independent_equal_variance_axes() -> None:
    observations = [
        _observation(
            frame_index=index,
            validity=ObservationValidity.VALID,
            scores=(("x", x), ("y", y)),
        )
        for index, (x, y) in enumerate(((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)))
    ]

    metrics = analyze_observations(observations)

    assert metrics.correlation_matrix["x"]["y"] == pytest.approx(0.0)
    assert metrics.effective_dimensionality == pytest.approx(2.0)


def test_cli_analysis_refuses_missing_observation_artifact_provenance(
    tmp_path: Path,
    valid_observations: list[BlendshapeObservation],
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    observations_path.write_text(
        "\n".join(
            json.dumps(observation.model_dump(mode="json"), sort_keys=True)
            for observation in valid_observations
        )
        + "\n",
        encoding="utf-8",
    )
    _write_manifest(
        tmp_path / "manifest.json",
        artifacts={},
        conclusion=None,
        acceptance_thresholds={"minimum_detection_rate": 0.7},
    )
    before_manifest = (tmp_path / "manifest.json").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="observations.jsonl"):
        passive_capture_main(["analyze", str(tmp_path)], stdout=io.StringIO())

    assert (tmp_path / "manifest.json").read_text(encoding="utf-8") == before_manifest
    assert not (tmp_path / "stability-metrics.json").exists()
    assert not (tmp_path / "phase-1-conclusion.md").exists()


def test_cli_analysis_refuses_observation_artifact_checksum_mismatch(
    tmp_path: Path,
    valid_observations: list[BlendshapeObservation],
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    observations_path.write_text(
        "\n".join(
            json.dumps(observation.model_dump(mode="json"), sort_keys=True)
            for observation in valid_observations
        )
        + "\n",
        encoding="utf-8",
    )
    _write_manifest(
        tmp_path / "manifest.json",
        artifacts={
            "observations.jsonl": {
                "path": "observations.jsonl",
                "sha256": "d" * 64,
                "size_bytes": observations_path.stat().st_size,
            }
        },
        conclusion=None,
        acceptance_thresholds={"minimum_detection_rate": 0.7},
    )
    before_manifest = (tmp_path / "manifest.json").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="checksum mismatch"):
        passive_capture_main(["analyze", str(tmp_path)], stdout=io.StringIO())

    assert (tmp_path / "manifest.json").read_text(encoding="utf-8") == before_manifest
    assert not (tmp_path / "stability-metrics.json").exists()
    assert not (tmp_path / "phase-1-conclusion.md").exists()


def test_cli_analysis_refuses_aborted_runs_without_mutating_manifest(
    tmp_path: Path,
    valid_observations: list[BlendshapeObservation],
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    observations_path.write_text(
        "\n".join(
            json.dumps(observation.model_dump(mode="json"), sort_keys=True)
            for observation in valid_observations
        )
        + "\n",
        encoding="utf-8",
    )
    _write_manifest(
        tmp_path / "manifest.json",
        status="aborted",
        artifacts={"observations.jsonl": _artifact_record_payload(observations_path)},
        conclusion=None,
        acceptance_thresholds={"minimum_detection_rate": 0.7},
    )
    before_manifest = (tmp_path / "manifest.json").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="completed"):
        passive_capture_main(["analyze", str(tmp_path)], stdout=io.StringIO())

    assert (tmp_path / "manifest.json").read_text(encoding="utf-8") == before_manifest
    assert json.loads(before_manifest)["conclusion"] is None
    assert not (tmp_path / "stability-metrics.json").exists()
    assert not (tmp_path / "phase-1-conclusion.md").exists()


def test_cli_analysis_writes_metrics_and_phase_1_conclusion(
    tmp_path: Path,
    valid_observations: list[BlendshapeObservation],
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    observations_path.write_text(
        "\n".join(
            json.dumps(observation.model_dump(mode="json"), sort_keys=True)
            for observation in valid_observations
        )
        + "\n",
        encoding="utf-8",
    )
    _write_manifest(
        tmp_path / "manifest.json",
        artifacts={"observations.jsonl": _artifact_record_payload(observations_path)},
        acceptance_thresholds={
            "minimum_detection_rate": 0.7,
            "categories": {
                "jawOpen": {"maximum_standard_deviation": 0.01},
                "mouthSmile": {"maximum_percentile_range": 0.4},
            },
        },
    )

    manifest_before = (tmp_path / "manifest.json").read_bytes()
    stdout = io.StringIO()
    exit_code = passive_capture_main(["analyze", str(tmp_path)], stdout=stdout)

    assert exit_code == 0
    assert "passive-001" in stdout.getvalue()
    assert "pass" in stdout.getvalue()

    assert (tmp_path / "manifest.json").read_bytes() == manifest_before
    generation_dirs = list((tmp_path / "analysis" / "generations").iterdir())
    assert len(generation_dirs) == 1
    generation_dir = generation_dirs[0]
    metrics_payload = json.loads(
        (generation_dir / "stability-metrics.json").read_text(encoding="utf-8")
    )
    assert metrics_payload["detection_rate"] == pytest.approx(0.75)
    assert metrics_payload["categories"]["mouthSmile"]["percentile_range"] == (
        pytest.approx(0.36)
    )

    conclusion_text = (generation_dir / "phase-1-conclusion.md").read_text(
        encoding="utf-8"
    )
    assert "Tripod at eye level, 45 cm from Alice." in conclusion_text
    assert "Overhead lab lights only." in conclusion_text
    assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in (
        conclusion_text
    )
    assert "Outcome: pass" in conclusion_text
    assert "Frame retention disabled." in conclusion_text
    assert "1 invalid observations" in conclusion_text

    analysis_manifest = json.loads(
        (generation_dir / "analysis-manifest.json").read_text(encoding="utf-8")
    )
    assert analysis_manifest["schema_version"] == "analysis-manifest/v1"
    assert analysis_manifest["analysis_kind"] == "stability"
    assert analysis_manifest["analyzer"]["package_version"]
    assert "git_revision" in analysis_manifest["analyzer"]
    assert (
        analysis_manifest["inputs"]["capture_manifest"]["sha256"]
        == sha256(manifest_before).hexdigest()
    )
    assert (
        analysis_manifest["inputs"]["observations"]["sha256"]
        == sha256(observations_path.read_bytes()).hexdigest()
    )
    for artifact_name in (
        "stability-metrics.json",
        "acceptance.json",
        "phase-1-conclusion.md",
    ):
        record = analysis_manifest["artifacts"][artifact_name]
        assert (
            sha256((generation_dir / artifact_name).read_bytes()).hexdigest()
            == (record["sha256"])
        )
