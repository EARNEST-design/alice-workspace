import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alice.analysis.blendshape_stability import analyze_observations, phase_1_acceptance
from alice.contracts import BlendshapeObservation, BlendshapeScore, ObservationValidity
from alice.experiments.manifest import ArtifactManifest
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
        scores=tuple(
            BlendshapeScore(name=name, score=score) for name, score in scores
        ),
    )


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
    manifest = ArtifactManifest(
        schema_version="artifact-manifest/v1",
        run_id="passive-001",
        status="completed",
        started_at=datetime(2026, 8, 31, 9, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 8, 31, 9, 2, 0, tzinfo=UTC),
        observation_count=4,
        config={
            "run_id": "passive-001",
            "camera_id": "alice-face-webcam",
            "requested_width": 640,
            "requested_height": 480,
            "requested_fps": 10,
            "duration_seconds": 120,
            "sample_count": 4,
            "sample_interval_ms": 100,
            "retain_frames": False,
            "operator_metadata": {
                "camera_placement": "Tripod at eye level, 45 cm from Alice.",
                "lighting": "Overhead lab lights only.",
            },
            "acceptance_thresholds": {
                "minimum_detection_rate": 0.7,
                "categories": {
                    "jawOpen": {"maximum_standard_deviation": 0.01},
                    "mouthSmile": {"maximum_percentile_range": 0.4},
                },
            },
        },
        artifacts={},
        git_revision="b" * 40,
        dependency_lock_path="uv.lock",
        dependency_lock_sha256="c" * 64,
        python_version="3.13.7",
        platform_system="Linux",
        platform_release="6.8.0",
        platform_machine="x86_64",
        aborted_reason=None,
        failure=None,
        conclusion=None,
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    exit_code = passive_capture_main(["analyze", str(tmp_path)], stdout=stdout)

    assert exit_code == 0
    assert "passive-001" in stdout.getvalue()
    assert "pass" in stdout.getvalue()

    metrics_payload = json.loads(
        (tmp_path / "stability-metrics.json").read_text(encoding="utf-8")
    )
    assert metrics_payload["detection_rate"] == pytest.approx(0.75)
    assert metrics_payload["categories"]["mouthSmile"]["percentile_range"] == (
        pytest.approx(0.36)
    )

    conclusion_text = (tmp_path / "phase-1-conclusion.md").read_text(encoding="utf-8")
    assert "Tripod at eye level, 45 cm from Alice." in conclusion_text
    assert "Overhead lab lights only." in conclusion_text
    assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in (
        conclusion_text
    )
    assert "Outcome: pass" in conclusion_text
    assert "Frame retention disabled." in conclusion_text
    assert "1 invalid observations" in conclusion_text

    updated_manifest = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    assert updated_manifest["conclusion"] == "Phase 1 acceptance: pass"
    assert "stability-metrics.json" in updated_manifest["artifacts"]
    assert "phase-1-conclusion.md" in updated_manifest["artifacts"]
