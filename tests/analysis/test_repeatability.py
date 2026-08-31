import io
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from alice.analysis.blendshape_stability import analyze_observations
from alice.analysis.repeatability import (
    RepeatabilityThresholds,
    compare_stability_runs,
    repeatability_acceptance,
)
from alice.contracts import BlendshapeObservation, BlendshapeScore, ObservationValidity
from alice.experiments.passive_capture import main as passive_main


def _observation(run_id: str, frame: int, score: float) -> BlendshapeObservation:
    captured_at = datetime(2026, 8, 31, 9, 0, tzinfo=UTC) + timedelta(
        milliseconds=100 * frame
    )
    return BlendshapeObservation(
        schema_version="blendshape-observation/v1",
        captured_at=captured_at,
        observed_at=captured_at + timedelta(milliseconds=5),
        monotonic_ns=100 + frame,
        camera_id="alice-face-webcam",
        run_id=run_id,
        detector="mediapipe-face-landmarker",
        detector_model_sha256="a" * 64,
        image_width=640,
        image_height=480,
        face_confidence=0.9,
        validity=ObservationValidity.VALID,
        invalid_reason=None,
        scores=(BlendshapeScore(name="jawOpen", score=score),),
    )


def _metrics(run_id: str, scores: tuple[float, ...]):
    return analyze_observations(
        [_observation(run_id, index, score) for index, score in enumerate(scores)]
    )


def test_compare_api_is_deterministic_and_order_independent() -> None:
    run_a = _metrics("run-a", (0.1, 0.2, 0.3))
    run_b = _metrics("run-b", (0.2, 0.3, 0.4))

    forward = compare_stability_runs([run_a, run_b])
    reverse = compare_stability_runs([run_b, run_a])

    assert forward == reverse
    assert forward.run_ids == ("run-a", "run-b")
    assert forward.categories["jawOpen"].means == {
        "run-a": pytest.approx(0.2),
        "run-b": pytest.approx(0.3),
    }
    assert forward.categories["jawOpen"].maximum_pairwise_mean_delta == (
        pytest.approx(0.1)
    )


def test_repeatability_acceptance_is_typed_and_failure_dominates_undefined() -> None:
    metrics = compare_stability_runs(
        [_metrics("run-a", (0.1, 0.2)), _metrics("run-b", (0.4, 0.5))]
    )
    result = repeatability_acceptance(
        metrics,
        {
            "maximum_mean_delta": {
                "jawOpen": 0.1,
                "missingCategory": 0.1,
            }
        },
    )

    assert result.outcome == "fail"
    assert [check.status for check in result.checks] == ["fail", "inconclusive"]
    assert result.failed_thresholds[0].metric_path == (
        "repeatability.jawOpen.maximum_pairwise_mean_delta"
    )


@pytest.mark.parametrize("invalid", [-1.0, 1.000001, float("inf"), float("nan")])
def test_repeatability_thresholds_reject_values_outside_finite_unit_interval(
    invalid: float,
) -> None:
    with pytest.raises(ValueError):
        RepeatabilityThresholds(maximum_mean_delta={"jawOpen": invalid})


def _write_run(run_dir: Path, run_id: str, scores: tuple[float, ...]) -> None:
    run_dir.mkdir()
    observations = [
        _observation(run_id, index, score) for index, score in enumerate(scores)
    ]
    observations_bytes = (
        "\n".join(
            json.dumps(observation.model_dump(mode="json"), sort_keys=True)
            for observation in observations
        )
        + "\n"
    ).encode()
    (run_dir / "observations.jsonl").write_bytes(observations_bytes)
    unavailable = {
        "availability": "unavailable",
        "value": None,
        "set_succeeded": None,
    }
    manifest = {
        "schema_version": "artifact-manifest/v1",
        "run_id": run_id,
        "status": "completed",
        "started_at": "2026-08-31T09:00:00Z",
        "ended_at": "2026-08-31T09:00:01Z",
        "observation_count": len(observations),
        "config": {
            "maximum_observation_age_ms": 250,
            "acceptance_thresholds": None,
            "repeatability_thresholds": {
                "maximum_mean_delta": {"jawOpen": 0.2}
            },
        },
        "artifacts": {
            "observations.jsonl": {
                "path": "observations.jsonl",
                "sha256": sha256(observations_bytes).hexdigest(),
                "size_bytes": len(observations_bytes),
            }
        },
        "git_revision": "b" * 40,
        "dependency_lock_path": "uv.lock",
        "dependency_lock_sha256": "c" * 64,
        "python_version": "3.13.7",
        "platform_system": "Linux",
        "platform_release": "6.8.0",
        "platform_machine": "x86_64",
        "camera_settings": {
            name: unavailable
            for name in ("width", "height", "fps", "focus", "exposure")
        },
        "aborted_reason": None,
        "failure": None,
        "conclusion": None,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_compare_cli_publishes_checksummed_repeatability_generation(
    tmp_path: Path,
) -> None:
    run_a = tmp_path / "run-a"
    run_b = tmp_path / "run-b"
    output_dir = tmp_path / "comparison"
    _write_run(run_a, "run-a", (0.1, 0.2, 0.3))
    _write_run(run_b, "run-b", (0.2, 0.3, 0.4))

    stdout = io.StringIO()
    exit_code = passive_main(
        [
            "compare",
            str(run_b),
            str(run_a),
            "--output-dir",
            str(output_dir),
        ],
        stdout=stdout,
    )

    assert exit_code == 0
    generation_dirs = list((output_dir / "analysis" / "generations").iterdir())
    assert len(generation_dirs) == 1
    generation_dir = generation_dirs[0]
    analysis_manifest = json.loads(
        (generation_dir / "analysis-manifest.json").read_text(encoding="utf-8")
    )
    assert analysis_manifest["analysis_kind"] == "repeatability"
    assert analysis_manifest["outcome"] == "pass"
    assert sorted(analysis_manifest["inputs"]) == [
        "run-a.capture_manifest",
        "run-a.observations",
        "run-b.capture_manifest",
        "run-b.observations",
    ]
    for artifact_name in ("repeatability-metrics.json", "acceptance.json"):
        record = analysis_manifest["artifacts"][artifact_name]
        assert sha256((generation_dir / artifact_name).read_bytes()).hexdigest() == (
            record["sha256"]
        )
    assert "pass" in stdout.getvalue()
