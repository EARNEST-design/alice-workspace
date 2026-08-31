"""Deterministic cross-run repeatability metrics and acceptance."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import combinations

from pydantic import BaseModel, ConfigDict, Field

from alice.analysis.blendshape_stability import (
    AcceptanceCheck,
    AcceptanceOutcome,
    AcceptanceResult,
    CheckStatus,
    StabilityMetrics,
)
from alice.contracts.blendshapes import NonEmptyString, Sha256Hex


class RepeatabilityThresholds(BaseModel):
    """Typed cross-run limits, keyed by exact blendshape category."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_mean_delta: dict[NonEmptyString, float] = Field(default_factory=dict)


class RepeatabilityCategoryMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    means: dict[NonEmptyString, float]
    maximum_pairwise_mean_delta: float | None = Field(default=None, ge=0.0)


class RepeatabilityMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_ids: tuple[NonEmptyString, ...]
    camera_id: NonEmptyString
    detector: NonEmptyString
    detector_model_sha256: Sha256Hex
    category_names: tuple[NonEmptyString, ...]
    categories: dict[NonEmptyString, RepeatabilityCategoryMetrics]


def compare_stability_runs(
    runs: Sequence[StabilityMetrics],
) -> RepeatabilityMetrics:
    """Compare runs deterministically, independent of caller ordering."""

    ordered = sorted(runs, key=lambda item: item.run_id or "")
    if len(ordered) < 2:
        raise ValueError("repeatability comparison requires at least two runs")
    if any(run.run_id is None for run in ordered):
        raise ValueError("repeatability runs require run_id")
    run_ids = tuple(run.run_id for run in ordered if run.run_id is not None)
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("repeatability run_id values must be unique")

    first = ordered[0]
    if (
        first.camera_id is None
        or first.detector is None
        or first.detector_model_sha256 is None
    ):
        raise ValueError("repeatability runs require complete detector identity")
    for run in ordered[1:]:
        if run.camera_id != first.camera_id:
            raise ValueError("repeatability camera_id mismatch")
        if run.detector != first.detector:
            raise ValueError("repeatability detector mismatch")
        if run.detector_model_sha256 != first.detector_model_sha256:
            raise ValueError("repeatability detector model mismatch")
        if run.category_names != first.category_names:
            raise ValueError("repeatability category schema mismatch")

    categories: dict[str, RepeatabilityCategoryMetrics] = {}
    for category_name in first.category_names:
        means = {
            run_id: run.categories[category_name].mean
            for run_id, run in zip(run_ids, ordered, strict=True)
        }
        deltas = [abs(left - right) for left, right in combinations(means.values(), 2)]
        categories[category_name] = RepeatabilityCategoryMetrics(
            means=means,
            maximum_pairwise_mean_delta=max(deltas) if deltas else None,
        )
    return RepeatabilityMetrics(
        run_ids=run_ids,
        camera_id=first.camera_id,
        detector=first.detector,
        detector_model_sha256=first.detector_model_sha256,
        category_names=first.category_names,
        categories=categories,
    )


def repeatability_acceptance(
    metrics: RepeatabilityMetrics,
    thresholds: RepeatabilityThresholds | Mapping[str, object],
) -> AcceptanceResult:
    """Evaluate typed mean-delta limits with failure-dominant semantics."""

    configured = (
        thresholds
        if isinstance(thresholds, RepeatabilityThresholds)
        else RepeatabilityThresholds.model_validate(thresholds)
    )
    checks: list[AcceptanceCheck] = []
    for category_name, expected in sorted(configured.maximum_mean_delta.items()):
        category = metrics.categories.get(category_name)
        observed = (
            None if category is None else category.maximum_pairwise_mean_delta
        )
        if observed is None:
            checks.append(
                AcceptanceCheck(
                    metric_path=(
                        f"repeatability.{category_name}.maximum_pairwise_mean_delta"
                    ),
                    comparator="<=",
                    expected=expected,
                    observed=None,
                    status=CheckStatus.INCONCLUSIVE,
                    message=f"repeatability category {category_name} is undefined",
                )
            )
            continue
        status = CheckStatus.PASS if observed <= expected else CheckStatus.FAIL
        checks.append(
            AcceptanceCheck(
                metric_path=(
                    f"repeatability.{category_name}.maximum_pairwise_mean_delta"
                ),
                comparator="<=",
                expected=expected,
                observed=observed,
                status=status,
                message=(
                    f"repeatability {category_name} mean delta <= {expected:.6f} "
                    f"(observed {observed:.6f})"
                ),
            )
        )
    failed = tuple(check for check in checks if check.status is CheckStatus.FAIL)
    undefined = any(check.status is CheckStatus.INCONCLUSIVE for check in checks)
    if failed:
        outcome = AcceptanceOutcome.FAIL
    elif not checks or undefined:
        outcome = AcceptanceOutcome.INCONCLUSIVE
    else:
        outcome = AcceptanceOutcome.PASS
    reasons = (
        ("one or more repeatability metrics were undefined",) if undefined else ()
    )
    if not checks:
        reasons = ("no repeatability thresholds configured",)
    return AcceptanceResult(
        outcome=outcome,
        checks=tuple(checks),
        failed_thresholds=failed,
        inconclusive_reasons=reasons,
    )
