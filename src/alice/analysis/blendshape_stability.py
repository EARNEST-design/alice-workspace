"""Deterministic offline stability analysis for passive blendshape runs."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from alice.contracts import BlendshapeObservation, ObservationValidity
from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.experiments.manifest import ArtifactManifest

_PERCENTILE_METHOD: Literal["linear"] = "linear"
_WARMUP_FRACTION = 0.1


class CategoryStabilityMetrics(BaseModel):
    """Deterministic descriptive statistics for one blendshape category."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int = Field(ge=0)
    mean: float = Field(ge=0.0, le=1.0)
    standard_deviation: float = Field(ge=0.0)
    median: float = Field(ge=0.0, le=1.0)
    percentile_05: float = Field(ge=0.0, le=1.0)
    percentile_95: float = Field(ge=0.0, le=1.0)
    percentile_range: float = Field(ge=0.0)
    warmup_drift: float | None = Field(default=None, ge=0.0)
    lag1_autocorrelation: float | None = Field(default=None, ge=-1.0, le=1.0)


class StabilityMetrics(BaseModel):
    """Offline metrics computed from a passive capture run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: NonEmptyString | None
    camera_id: NonEmptyString | None
    detector: NonEmptyString | None
    detector_model_sha256: Sha256Hex | None
    total_frames: int = Field(ge=0)
    valid_frames: int = Field(ge=0)
    detection_rate: float = Field(ge=0.0, le=1.0)
    category_names: tuple[NonEmptyString, ...]
    categories: dict[NonEmptyString, CategoryStabilityMetrics]
    invalid_reasons: dict[NonEmptyString, int]


class CategoryAcceptanceThresholds(BaseModel):
    """Configured pass/fail gates for one category."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_standard_deviation: float | None = Field(default=None, ge=0.0)
    maximum_percentile_range: float | None = Field(default=None, ge=0.0)
    maximum_warmup_drift: float | None = Field(default=None, ge=0.0)
    minimum_lag1_autocorrelation: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
    )


class AcceptanceThresholds(BaseModel):
    """Configured Phase 1 acceptance thresholds loaded from run config."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_detection_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    categories: dict[NonEmptyString, CategoryAcceptanceThresholds] = Field(
        default_factory=dict
    )


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class AcceptanceCheck(BaseModel):
    """One evaluated acceptance threshold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_path: NonEmptyString
    comparator: Literal[">=", "<="]
    expected: float
    observed: float | None
    status: CheckStatus
    message: NonEmptyString


class AcceptanceOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class AcceptanceResult(BaseModel):
    """Overall Phase 1 acceptance outcome and threshold details."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: AcceptanceOutcome
    checks: tuple[AcceptanceCheck, ...]
    failed_thresholds: tuple[AcceptanceCheck, ...]
    inconclusive_reasons: tuple[NonEmptyString, ...] = ()


def analyze_observations(
    observations: Sequence[BlendshapeObservation],
) -> StabilityMetrics:
    """Compute deterministic per-category stability metrics from observations."""

    observation_list = list(observations)
    identity = _resolve_identity(observation_list)
    invalid_reasons = Counter(
        observation.invalid_reason
        for observation in observation_list
        if observation.invalid_reason is not None
    )
    valid_observations = [
        observation
        for observation in observation_list
        if observation.validity is ObservationValidity.VALID
    ]
    category_names = _expected_category_names(valid_observations)

    category_scores: dict[str, list[float]] = {name: [] for name in category_names}
    for observation in valid_observations:
        names = tuple(score.name for score in observation.scores)
        if names != category_names:
            raise ValueError(
                "blendshape category schema mismatch: "
                f"expected={list(category_names)} observed={list(names)}"
            )
        for score in observation.scores:
            category_scores[score.name].append(score.score)

    total_frames = len(observation_list)
    valid_frames = len(valid_observations)
    categories = {
        name: _category_metrics(values)
        for name, values in category_scores.items()
    }
    detection_rate = 0.0 if total_frames == 0 else valid_frames / total_frames
    return StabilityMetrics(
        run_id=identity["run_id"],
        camera_id=identity["camera_id"],
        detector=identity["detector"],
        detector_model_sha256=identity["detector_model_sha256"],
        total_frames=total_frames,
        valid_frames=valid_frames,
        detection_rate=detection_rate,
        category_names=category_names,
        categories=categories,
        invalid_reasons=dict(sorted(invalid_reasons.items())),
    )


def analyze_stability(run_dir: Path) -> StabilityMetrics:
    """Load Phase 1 artifacts from a run directory and compute stability metrics."""

    run_path = run_dir.resolve()
    manifest = ArtifactManifest.model_validate_json(
        (run_path / "manifest.json").read_text(encoding="utf-8")
    )
    if manifest.status != "completed":
        raise ValueError(
            "only completed manifests may be analyzed: "
            f"status={manifest.status.value}"
        )

    observations_path = _verified_observations_path(manifest, run_path)
    observations = _load_observations(observations_path)
    metrics = analyze_observations(observations)

    if manifest.observation_count != metrics.total_frames:
        raise ValueError(
            "manifest observation_count does not match observations.jsonl: "
            f"{manifest.observation_count} != {metrics.total_frames}"
        )
    if metrics.run_id is not None and metrics.run_id != manifest.run_id:
        raise ValueError(
            "manifest run_id does not match observation run_id: "
            f"{manifest.run_id!r} != {metrics.run_id!r}"
        )
    return metrics


def phase_1_acceptance(
    metrics: StabilityMetrics,
    thresholds: AcceptanceThresholds | Mapping[str, Any] | None,
) -> AcceptanceResult:
    """Evaluate configured thresholds and return pass/fail/inconclusive."""

    if thresholds is None:
        return AcceptanceResult(
            outcome=AcceptanceOutcome.INCONCLUSIVE,
            checks=(),
            failed_thresholds=(),
            inconclusive_reasons=("no acceptance thresholds configured",),
        )

    configured = (
        thresholds
        if isinstance(thresholds, AcceptanceThresholds)
        else AcceptanceThresholds.model_validate(thresholds)
    )
    checks: list[AcceptanceCheck] = []

    if configured.minimum_detection_rate is not None:
        checks.append(
            _evaluate_check(
                metric_path="detection_rate",
                comparator=">=",
                expected=configured.minimum_detection_rate,
                observed=metrics.detection_rate,
            )
        )

    if metrics.valid_frames == 0:
        for category_name, category_thresholds in configured.categories.items():
            checks.extend(
                _inconclusive_category_checks(
                    category_name,
                    category_thresholds,
                    "no valid frames available for stability analysis",
                )
            )
        return AcceptanceResult(
            outcome=AcceptanceOutcome.INCONCLUSIVE,
            checks=tuple(checks),
            failed_thresholds=tuple(
                check for check in checks if check.status is CheckStatus.FAIL
            ),
            inconclusive_reasons=("no valid frames available for stability analysis",),
        )

    for category_name, category_thresholds in configured.categories.items():
        if category_name not in metrics.categories:
            raise ValueError(
                f"acceptance thresholds reference unknown category: {category_name}"
            )
        category_metrics = metrics.categories[category_name]
        if category_thresholds.maximum_standard_deviation is not None:
            checks.append(
                _evaluate_check(
                    metric_path=f"{category_name}.standard_deviation",
                    comparator="<=",
                    expected=category_thresholds.maximum_standard_deviation,
                    observed=category_metrics.standard_deviation,
                )
            )
        if category_thresholds.maximum_percentile_range is not None:
            checks.append(
                _evaluate_check(
                    metric_path=f"{category_name}.percentile_range",
                    comparator="<=",
                    expected=category_thresholds.maximum_percentile_range,
                    observed=category_metrics.percentile_range,
                )
            )
        if category_thresholds.maximum_warmup_drift is not None:
            checks.append(
                _evaluate_check(
                    metric_path=f"{category_name}.warmup_drift",
                    comparator="<=",
                    expected=category_thresholds.maximum_warmup_drift,
                    observed=category_metrics.warmup_drift,
                )
            )
        if category_thresholds.minimum_lag1_autocorrelation is not None:
            checks.append(
                _evaluate_check(
                    metric_path=f"{category_name}.lag1_autocorrelation",
                    comparator=">=",
                    expected=category_thresholds.minimum_lag1_autocorrelation,
                    observed=category_metrics.lag1_autocorrelation,
                )
            )

    if not checks:
        return AcceptanceResult(
            outcome=AcceptanceOutcome.INCONCLUSIVE,
            checks=(),
            failed_thresholds=(),
            inconclusive_reasons=("no acceptance thresholds configured",),
        )

    inconclusive_reasons: list[str] = []
    if metrics.valid_frames == 0:
        inconclusive_reasons.append("no valid frames available for stability analysis")
    if any(check.status is CheckStatus.INCONCLUSIVE for check in checks):
        inconclusive_reasons.append("one or more threshold metrics were undefined")

    failed_thresholds = tuple(
        check for check in checks if check.status is CheckStatus.FAIL
    )
    if inconclusive_reasons:
        outcome = AcceptanceOutcome.INCONCLUSIVE
    elif failed_thresholds:
        outcome = AcceptanceOutcome.FAIL
    else:
        outcome = AcceptanceOutcome.PASS

    return AcceptanceResult(
        outcome=outcome,
        checks=tuple(checks),
        failed_thresholds=failed_thresholds,
        inconclusive_reasons=tuple(inconclusive_reasons),
    )


def _resolve_identity(
    observations: Sequence[BlendshapeObservation],
) -> dict[str, str | None]:
    if not observations:
        return {
            "run_id": None,
            "camera_id": None,
            "detector": None,
            "detector_model_sha256": None,
        }

    first = observations[0]
    identity: dict[str, str | None] = {
        "run_id": first.run_id,
        "camera_id": first.camera_id,
        "detector": first.detector,
        "detector_model_sha256": first.detector_model_sha256,
    }
    for observation in observations[1:]:
        if observation.run_id != identity["run_id"]:
            raise ValueError("observation run_id mismatch within the same run")
        if observation.camera_id != identity["camera_id"]:
            raise ValueError("observation camera_id mismatch within the same run")
        if observation.detector != identity["detector"]:
            raise ValueError("observation detector mismatch within the same run")
        if observation.detector_model_sha256 != identity["detector_model_sha256"]:
            raise ValueError(
                "observation detector_model_sha256 mismatch within the same run"
            )
    return identity


def _expected_category_names(
    observations: Sequence[BlendshapeObservation],
) -> tuple[str, ...]:
    for observation in observations:
        if observation.validity is ObservationValidity.VALID:
            return tuple(score.name for score in observation.scores)
    return ()


def _verified_observations_path(manifest: ArtifactManifest, run_path: Path) -> Path:
    artifact = manifest.artifacts.get("observations.jsonl")
    if artifact is None:
        raise ValueError("manifest observations.jsonl artifact entry is required")
    if artifact.path != "observations.jsonl":
        raise ValueError(
            "manifest observations.jsonl artifact must reference observations.jsonl"
        )

    observations_path = run_path / artifact.path
    if not observations_path.is_file():
        raise ValueError(f"recorded observations artifact is missing: {artifact.path}")

    observed_size = observations_path.stat().st_size
    if observed_size != artifact.size_bytes:
        raise ValueError(
            "observations.jsonl size mismatch: "
            f"manifest={artifact.size_bytes} actual={observed_size}"
        )

    observed_sha256 = _sha256_path(observations_path)
    if observed_sha256 != artifact.sha256:
        raise ValueError(
            "observations.jsonl checksum mismatch: "
            f"manifest={artifact.sha256} actual={observed_sha256}"
        )
    return observations_path


def _category_metrics(values: Sequence[float]) -> CategoryStabilityMetrics:
    score_array = np.asarray(values, dtype=float)
    count = int(score_array.size)
    if count == 0:
        raise ValueError("category metrics require at least one score")

    percentiles = np.percentile(
        score_array,
        np.asarray([5.0, 95.0], dtype=float),
        method=_PERCENTILE_METHOD,
    )
    percentile_05 = float(percentiles[0])
    percentile_95 = float(percentiles[1])
    return CategoryStabilityMetrics(
        count=count,
        mean=float(np.mean(score_array)),
        standard_deviation=float(np.std(score_array, ddof=0)),
        median=float(np.median(score_array)),
        percentile_05=percentile_05,
        percentile_95=percentile_95,
        percentile_range=float(percentile_95 - percentile_05),
        warmup_drift=_warmup_drift(score_array),
        lag1_autocorrelation=_lag1_autocorrelation(score_array),
    )


def _inconclusive_category_checks(
    category_name: str,
    thresholds: CategoryAcceptanceThresholds,
    reason: str,
) -> list[AcceptanceCheck]:
    checks: list[AcceptanceCheck] = []
    if thresholds.maximum_standard_deviation is not None:
        checks.append(
            _inconclusive_check(
                metric_path=f"{category_name}.standard_deviation",
                comparator="<=",
                expected=thresholds.maximum_standard_deviation,
                reason=reason,
            )
        )
    if thresholds.maximum_percentile_range is not None:
        checks.append(
            _inconclusive_check(
                metric_path=f"{category_name}.percentile_range",
                comparator="<=",
                expected=thresholds.maximum_percentile_range,
                reason=reason,
            )
        )
    if thresholds.maximum_warmup_drift is not None:
        checks.append(
            _inconclusive_check(
                metric_path=f"{category_name}.warmup_drift",
                comparator="<=",
                expected=thresholds.maximum_warmup_drift,
                reason=reason,
            )
        )
    if thresholds.minimum_lag1_autocorrelation is not None:
        checks.append(
            _inconclusive_check(
                metric_path=f"{category_name}.lag1_autocorrelation",
                comparator=">=",
                expected=thresholds.minimum_lag1_autocorrelation,
                reason=reason,
            )
        )
    return checks


def _inconclusive_check(
    *,
    metric_path: str,
    comparator: Literal[">=", "<="],
    expected: float,
    reason: str,
) -> AcceptanceCheck:
    return AcceptanceCheck(
        metric_path=metric_path,
        comparator=comparator,
        expected=expected,
        observed=None,
        status=CheckStatus.INCONCLUSIVE,
        message=f"{metric_path} is undefined: {reason}",
    )


def _warmup_drift(score_array: np.ndarray) -> float | None:
    count = int(score_array.size)
    if count < 2:
        return None

    warmup_count = max(1, int(np.ceil(count * _WARMUP_FRACTION)))
    if warmup_count >= count:
        return None
    return float(
        abs(np.mean(score_array[:warmup_count]) - np.mean(score_array[warmup_count:]))
    )


def _lag1_autocorrelation(score_array: np.ndarray) -> float | None:
    count = int(score_array.size)
    if count < 3:
        return None

    leading = score_array[:-1]
    lagged = score_array[1:]
    if np.allclose(leading, leading[0]) or np.allclose(lagged, lagged[0]):
        return None
    return float(np.corrcoef(leading, lagged)[0, 1])


def _sha256_path(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evaluate_check(
    *,
    metric_path: str,
    comparator: Literal[">=", "<="],
    expected: float,
    observed: float | None,
) -> AcceptanceCheck:
    if observed is None:
        return AcceptanceCheck(
            metric_path=metric_path,
            comparator=comparator,
            expected=expected,
            observed=None,
            status=CheckStatus.INCONCLUSIVE,
            message=f"{metric_path} is undefined",
        )

    if comparator == ">=":
        passed = observed >= expected
    else:
        passed = observed <= expected
    status = CheckStatus.PASS if passed else CheckStatus.FAIL
    return AcceptanceCheck(
        metric_path=metric_path,
        comparator=comparator,
        expected=expected,
        observed=observed,
        status=status,
        message=(
            f"{metric_path} {comparator} {expected:.6f} "
            f"(observed {observed:.6f})"
        ),
    )


def _load_observations(path: Path) -> list[BlendshapeObservation]:
    observations: list[BlendshapeObservation] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        observations.append(BlendshapeObservation.model_validate(json.loads(line)))
    return observations
