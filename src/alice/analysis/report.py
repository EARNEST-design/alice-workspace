"""Rendering for packaged Phase 1 analysis reports."""

from __future__ import annotations

from importlib.resources import files
from typing import Any

from alice.analysis.blendshape_stability import (
    AcceptanceCheck,
    AcceptanceResult,
    StabilityMetrics,
)
from alice.experiments.manifest import ArtifactManifest


def render_phase_1_conclusion(
    *,
    manifest: ArtifactManifest,
    metrics: StabilityMetrics,
    acceptance: AcceptanceResult,
) -> str:
    template = (
        files("alice.resources")
        .joinpath("passive-blendshape-conclusion.md")
        .read_text(encoding="utf-8")
    )
    setup = manifest.config.get("setup")
    setup_mapping = setup if isinstance(setup, dict) else {}
    return template.format(
        outcome=acceptance.outcome,
        run_id=manifest.run_id,
        camera_id=metrics.camera_id or manifest.config.get("camera_id", "unknown"),
        camera_placement=_condition_detail(setup_mapping, "placement"),
        lighting=_condition_detail(setup_mapping, "lighting"),
        focus=_condition_detail(setup_mapping, "focus"),
        exposure=_condition_detail(setup_mapping, "exposure"),
        detector=metrics.detector or "unknown",
        detector_model_sha256=metrics.detector_model_sha256 or "unknown",
        privacy_retention=_privacy_retention_summary(manifest.config),
        threshold_lines=_format_threshold_lines(acceptance),
        effective_dimensionality=f"{metrics.effective_dimensionality:.6f}",
        anomaly_lines=_format_anomaly_lines(metrics, acceptance),
    )


def _condition_detail(setup: dict[str, Any], name: str) -> str:
    condition = setup.get(name)
    if not isinstance(condition, dict):
        return "not recorded"
    state = condition.get("state", "unknown")
    detail = condition.get("detail", "not recorded")
    return f"{state}: {detail}"


def _privacy_retention_summary(config: dict[str, Any]) -> str:
    policy = config.get("retention_policy")
    if not isinstance(policy, dict):
        return "Retention policy not recorded."
    if policy.get("mode") == "derived_observations_only":
        return (
            "Frame retention disabled. Derived observations retained under "
            f"policy {policy.get('policy_id', 'unknown')}."
        )
    approval = policy.get("raw_approval")
    approval_id = approval.get("approval_id") if isinstance(approval, dict) else None
    return f"Raw frame retention enabled with approval {approval_id or 'unknown'}."


def _format_threshold_lines(acceptance: AcceptanceResult) -> str:
    if not acceptance.checks:
        return "- No thresholds configured."
    return "\n".join(_format_check_line(check) for check in acceptance.checks)


def _format_check_line(check: AcceptanceCheck) -> str:
    observed = "undefined" if check.observed is None else f"{check.observed:.6f}"
    return (
        f"- {check.metric_path}: expected {check.comparator} {check.expected:.6f}; "
        f"observed {observed}; status {check.status}"
    )


def _format_anomaly_lines(
    metrics: StabilityMetrics,
    acceptance: AcceptanceResult,
) -> str:
    anomalies: list[str] = []
    invalid_count = metrics.total_frames - metrics.valid_frames
    if invalid_count:
        details = ", ".join(
            f"{reason}={count}" for reason, count in metrics.invalid_reasons.items()
        )
        anomalies.append(f"- {invalid_count} invalid observations ({details}).")
    for name, category in metrics.categories.items():
        if category.lag1_autocorrelation is None:
            anomalies.append(
                f"- {name} lag-1 autocorrelation was undefined because the series "
                "was too short or constant."
            )
    anomalies.extend(f"- {reason}." for reason in acceptance.inconclusive_reasons)
    if not anomalies:
        anomalies.append("- None observed.")
    return "\n".join(anomalies)
