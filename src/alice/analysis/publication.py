"""Transactional publication of versioned analysis generations."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import uuid4

from alice.analysis.blendshape_stability import (
    AcceptanceThresholds,
    analyze_stability,
    phase_1_acceptance,
)
from alice.analysis.manifest import (
    AnalysisInput,
    AnalysisManifest,
    AnalyzerIdentity,
)
from alice.analysis.repeatability import (
    RepeatabilityThresholds,
    compare_stability_runs,
    repeatability_acceptance,
)
from alice.analysis.report import render_phase_1_conclusion
from alice.experiments.artifact_store import publish_generation
from alice.experiments.manifest import ArtifactManifest, ArtifactRecord


def publish_stability_analysis(run_dir: Path) -> tuple[AnalysisManifest, Path]:
    """Analyze a capture and atomically publish a separate immutable generation."""

    resolved_run_dir = run_dir.resolve()
    capture_manifest_path = resolved_run_dir / "manifest.json"
    capture_manifest_bytes = capture_manifest_path.read_bytes()
    capture_manifest = ArtifactManifest.model_validate_json(capture_manifest_bytes)
    metrics = analyze_stability(resolved_run_dir)
    thresholds_payload = capture_manifest.config.get("acceptance_thresholds")
    thresholds = (
        None
        if thresholds_payload is None
        else AcceptanceThresholds.model_validate(thresholds_payload)
    )
    acceptance = phase_1_acceptance(metrics, thresholds)
    conclusion = render_phase_1_conclusion(
        manifest=capture_manifest,
        metrics=metrics,
        acceptance=acceptance,
    )
    threshold_value = None if thresholds is None else thresholds.model_dump(mode="json")
    threshold_bytes = _canonical_json_bytes(threshold_value)
    payloads = {
        "stability-metrics.json": _pretty_json_bytes(metrics.model_dump(mode="json")),
        "acceptance.json": _pretty_json_bytes(acceptance.model_dump(mode="json")),
        "phase-1-conclusion.md": conclusion.encode("utf-8"),
    }
    observation_record = capture_manifest.artifacts["observations.jsonl"]
    generated_at = datetime.now(UTC)
    generation_id = (
        f"stability-{generated_at.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex}"
    )
    analysis_manifest = AnalysisManifest(
        schema_version="analysis-manifest/v1",
        generation_id=generation_id,
        analysis_kind="stability",
        generated_at=generated_at,
        analyzer=AnalyzerIdentity(
            package_version=_package_version(),
            git_revision=_git_revision(),
        ),
        inputs={
            "capture_manifest": _input_record(
                "manifest.json",
                capture_manifest_bytes,
            ),
            "observations": AnalysisInput(
                path=observation_record.path,
                sha256=observation_record.sha256,
                size_bytes=observation_record.size_bytes,
            ),
        },
        thresholds=threshold_value,
        thresholds_sha256=sha256(threshold_bytes).hexdigest(),
        artifacts={
            name: _artifact_record(name, payload) for name, payload in payloads.items()
        },
        outcome=acceptance.outcome.value,
    )
    payloads["analysis-manifest.json"] = _pretty_json_bytes(
        analysis_manifest.model_dump(mode="json")
    )
    generation_dir = publish_generation(
        resolved_run_dir / "analysis" / "generations",
        generation_id,
        payloads,
    )
    return analysis_manifest, generation_dir


def publish_repeatability_analysis(
    run_dirs: list[Path],
    output_dir: Path,
) -> tuple[AnalysisManifest, Path]:
    """Compare capture runs and publish checksummed machine-readable evidence."""

    if len(run_dirs) < 2:
        raise ValueError("repeatability comparison requires at least two run dirs")
    manifests: list[tuple[Path, bytes, ArtifactManifest]] = []
    stability_metrics = []
    threshold_payloads: list[object] = []
    for run_dir in run_dirs:
        resolved = run_dir.resolve()
        manifest_bytes = (resolved / "manifest.json").read_bytes()
        manifest = ArtifactManifest.model_validate_json(manifest_bytes)
        manifests.append((resolved, manifest_bytes, manifest))
        stability_metrics.append(analyze_stability(resolved))
        threshold_payloads.append(manifest.config.get("repeatability_thresholds"))
    canonical_thresholds = {_canonical_json_bytes(item) for item in threshold_payloads}
    if len(canonical_thresholds) != 1 or threshold_payloads[0] is None:
        raise ValueError("all runs require identical typed repeatability_thresholds")
    thresholds = RepeatabilityThresholds.model_validate(threshold_payloads[0])
    metrics = compare_stability_runs(stability_metrics)
    acceptance = repeatability_acceptance(metrics, thresholds)
    threshold_value = thresholds.model_dump(mode="json")
    threshold_bytes = _canonical_json_bytes(threshold_value)
    payloads = {
        "repeatability-metrics.json": _pretty_json_bytes(
            metrics.model_dump(mode="json")
        ),
        "acceptance.json": _pretty_json_bytes(acceptance.model_dump(mode="json")),
    }
    inputs: dict[str, AnalysisInput] = {}
    for resolved, manifest_bytes, manifest in sorted(
        manifests,
        key=lambda item: item[2].run_id,
    ):
        inputs[f"{manifest.run_id}.capture_manifest"] = _input_record(
            "manifest.json",
            manifest_bytes,
        )
        observation = manifest.artifacts["observations.jsonl"]
        inputs[f"{manifest.run_id}.observations"] = AnalysisInput(
            path=observation.path,
            sha256=observation.sha256,
            size_bytes=observation.size_bytes,
        )
    generated_at = datetime.now(UTC)
    generation_id = (
        f"repeatability-{generated_at.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex}"
    )
    analysis_manifest = AnalysisManifest(
        schema_version="analysis-manifest/v1",
        generation_id=generation_id,
        analysis_kind="repeatability",
        generated_at=generated_at,
        analyzer=AnalyzerIdentity(
            package_version=_package_version(),
            git_revision=_git_revision(),
        ),
        inputs=inputs,
        thresholds=threshold_value,
        thresholds_sha256=sha256(threshold_bytes).hexdigest(),
        artifacts={
            name: _artifact_record(name, payload) for name, payload in payloads.items()
        },
        outcome=acceptance.outcome.value,
    )
    payloads["analysis-manifest.json"] = _pretty_json_bytes(
        analysis_manifest.model_dump(mode="json")
    )
    generation_dir = publish_generation(
        output_dir.resolve() / "analysis" / "generations",
        generation_id,
        payloads,
    )
    return analysis_manifest, generation_dir


def _input_record(path: str, payload: bytes) -> AnalysisInput:
    return AnalysisInput(
        path=path,
        sha256=sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def _artifact_record(path: str, payload: bytes) -> ArtifactRecord:
    return ArtifactRecord(
        path=path,
        sha256=sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _pretty_json_bytes(payload: object) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _package_version() -> str:
    try:
        return version("alice")
    except PackageNotFoundError:
        return "0+unknown"


def _git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None
