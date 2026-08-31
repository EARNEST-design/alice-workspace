"""Deterministic, provenance-checked actuator system-identification analysis."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Callable, Final, Literal, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from alice.contracts.actuation import (
    ActuatorStatus,
    ActuatorStatusState,
    ActuatorTarget,
    PoseRequest,
)
from alice.contracts.blendshapes import BlendshapeObservation, NonEmptyString, Sha256Hex
from alice.experiments.artifact_store import publish_generation
from alice.experiments.manifest import (
    ArtifactManifest,
    ArtifactRecord,
    IdentificationObserverProvenance,
    NegotiatedCameraSettings,
    RunKind,
    RunStatus,
)
from alice.experiments.system_identification import (
    ControllerSettlingDecision,
    IdentificationStep,
    VisualSettlingDecision,
)

BOOTSTRAP_SEED: Final[Literal[20260831]] = 20260831
BOOTSTRAP_REPLICATES = 2000
ANALYZER_REVISION: Final[Literal["system-identification/v2"]] = (
    "system-identification/v2"
)
VARIANCE_DEFINITION_REVISION: Final[Literal["population-ddof0/v1"]] = (
    "population-ddof0/v1"
)
VARIANCE_DEFINITION: Final[str] = "mean((x - mean(x))^2); divisor N; ddof=0"
MONOTONICITY_DEFINITION: Final[str] = (
    "mean sign agreement of Home-to-positive and Home-to-negative responses "
    "with their commanded directions; range [0, 1]"
)
ASYMMETRY_DEFINITION: Final[str] = (
    "abs(abs(positive one-sided slope) - abs(negative one-sided slope))"
)
SATURATION_DEFINITION_REVISION: Final[Literal["outer-inner-slope-ratio/v1"]] = (
    "outer-inner-slope-ratio/v1"
)
SATURATION_DEFINITION: Final[str] = (
    "1 - abs(outer incremental slope) / abs(inner Home-to-offset slope); "
    "positive values indicate slope compression"
)
SNR_FORMULA_REVISION: Final[Literal["session-effect-rss-population/v2"]] = (
    "session-effect-rss-population/v2"
)
SNR_FORMULA: Final[str] = (
    "signal = mean_session(abs(mean_positive_session - mean_negative_session) / 2); "
    "noise_sd = sqrt(sum_group(sum((x - mean_group)^2)) / "
    "sum(n_group)); SNR = signal / noise_sd; population variance ddof=0"
)
HYSTERESIS_DEFINITION: Final[str] = (
    "Home-return hysteresis proxy: within each session, compare absolute mean "
    "blendshape differences between Home immediately after the positive excursion "
    "and Home immediately after the negative excursion. This is not a full "
    "same-target bidirectional loop characterization."
)


class IdentificationSample(BaseModel):
    """One named blendshape sample at a known semantic actuator position."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: NonEmptyString
    step_id: NonEmptyString
    sequence_index: int = Field(ge=0)
    actuator_name: NonEmptyString
    normalized_position: Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
    phase: Literal["home", "positive", "negative"]
    blendshapes: Mapping[NonEmptyString, Annotated[float, Field(allow_inf_nan=False)]]
    controller_command_ns: int | None = Field(default=None, ge=0)
    controller_settled_ns: int | None = Field(default=None, ge=0)
    first_visual_sample_ns: int | None = Field(default=None, ge=0)
    visual_settled_ns: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_sample(self) -> IdentificationSample:
        if not self.blendshapes:
            raise ValueError("blendshapes must not be empty")
        if self.phase == "home" and self.normalized_position != 0.0:
            raise ValueError("home sample must use zero position")
        if self.phase == "positive" and self.normalized_position <= 0:
            raise ValueError("positive sample must use positive position")
        if self.phase == "negative" and self.normalized_position >= 0:
            raise ValueError("negative sample must use negative position")
        return self


class _CommandEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    step: IdentificationStep
    monotonic_ns: int = Field(ge=0)
    request: PoseRequest


class _StatusEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    monotonic_ns: int = Field(ge=0)
    status: ActuatorStatus


class ConfidenceInterval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lower: Annotated[float, Field(allow_inf_nan=False)]
    upper: Annotated[float, Field(allow_inf_nan=False)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.95


class MetricValue(BaseModel):
    """A numeric estimate whose absence and uncertainty are never encoded as zero."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    value: Annotated[float, Field(allow_inf_nan=False)] | None
    status: Literal["estimated", "missing", "undefined"]
    reason: NonEmptyString | None = None
    confidence_interval: ConfidenceInterval | None = None
    uncertainty_status: Literal["estimated", "unavailable"]
    uncertainty_reason: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_state(self) -> MetricValue:
        if (self.status == "estimated") != (self.value is not None):
            raise ValueError("estimated status and value presence must agree")
        if self.status != "estimated" and self.reason is None:
            raise ValueError("missing or undefined metric requires a reason")
        if self.uncertainty_status == "unavailable" and self.uncertainty_reason is None:
            raise ValueError("unavailable uncertainty requires a reason")
        return self


class MatrixCell(MetricValue):
    row: NonEmptyString
    column: NonEmptyString


class NamedMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    row_labels: tuple[NonEmptyString, ...]
    column_labels: tuple[NonEmptyString, ...]
    cells: tuple[MatrixCell, ...]

    @model_validator(mode="after")
    def validate_cells(self) -> NamedMatrix:
        expected = {
            (row, column) for row in self.row_labels for column in self.column_labels
        }
        actual = {(cell.row, cell.column) for cell in self.cells}
        if len(actual) != len(self.cells) or actual != expected:
            raise ValueError("matrix cells must exactly cover unique named axes")
        return self


class IdentificationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["identification-metrics/v2"]
    bootstrap_seed: Literal[20260831]
    bootstrap_replicates: int
    session_ids: tuple[NonEmptyString, ...]
    variance_definition_revision: Literal["population-ddof0/v1"]
    variance_definition: NonEmptyString
    jacobian: NamedMatrix
    baseline_variance: NamedMatrix
    between_session_baseline_variance: NamedMatrix
    position_variance: NamedMatrix
    between_session_position_variance: NamedMatrix
    within_position_noise_sd: NamedMatrix
    signal_to_noise: NamedMatrix
    snr_formula_revision: Literal["session-effect-rss-population/v2"]
    snr_formula: NonEmptyString
    hysteresis: NamedMatrix
    hysteresis_definition: NonEmptyString
    monotonicity: NamedMatrix
    monotonicity_definition: NonEmptyString
    asymmetry: NamedMatrix
    asymmetry_definition: NonEmptyString
    saturation: NamedMatrix
    saturation_definition_revision: Literal["outer-inner-slope-ratio/v1"]
    saturation_definition: NonEmptyString
    return_to_home_drift: NamedMatrix
    cross_effects: NamedMatrix
    actuator_coupling: NamedMatrix
    controller_settling_ms: MetricValue
    command_to_first_visual_ms: MetricValue
    visual_window_settling_ms: MetricValue
    total_command_to_visual_settled_ms: MetricValue
    rank: int | None = Field(default=None, ge=0)
    singular_values: tuple[Annotated[float, Field(ge=0, allow_inf_nan=False)], ...]
    condition_number: MetricValue
    warnings: tuple[str, ...]


class RepeatabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["identification-repeatability/v1"]
    reference_seed: int
    repeat_seed: int
    reference_run_id: NonEmptyString
    repeat_run_id: NonEmptyString
    group_assignments: tuple[RunGroupAssignment, RunGroupAssignment]
    absolute_delta: NamedMatrix
    checks: tuple[RepeatabilityCheck, ...] = ()
    outcome: Literal["pass", "fail", "inconclusive"]
    warnings: tuple[str, ...]

    @model_validator(mode="after")
    def validate_outcome(self) -> RepeatabilityResult:
        if any(check.passed is False for check in self.checks):
            expected = "fail"
        elif not self.checks or any(check.passed is None for check in self.checks):
            expected = "inconclusive"
        else:
            expected = "pass"
        if self.outcome != expected:
            raise ValueError("repeatability outcome contradicts typed checks")
        return self


class JacobianRepeatabilityThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    blendshape_name: NonEmptyString
    actuator_name: NonEmptyString
    maximum_absolute_delta: Annotated[float, Field(ge=0, allow_inf_nan=False)]


class RepeatabilityThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["identification-repeatability-thresholds/v1"]
    cells: tuple[JacobianRepeatabilityThreshold, ...] = ()
    maximum_mean_absolute_delta: (
        Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    ) = None

    @model_validator(mode="after")
    def validate_thresholds(self) -> RepeatabilityThresholds:
        identities = [(item.blendshape_name, item.actuator_name) for item in self.cells]
        if len(identities) != len(set(identities)):
            raise ValueError("repeatability threshold cells must be unique")
        if not self.cells and self.maximum_mean_absolute_delta is None:
            raise ValueError("at least one repeatability threshold is required")
        return self


class RepeatabilityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    check_id: NonEmptyString
    observed: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    maximum: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    passed: bool | None
    reason: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_check(self) -> RepeatabilityCheck:
        if (self.observed is None) != (self.passed is None):
            raise ValueError("observed and passed availability must agree")
        if self.observed is None and self.reason is None:
            raise ValueError("unevaluable repeatability check requires a reason")
        if self.observed is not None and self.passed != (self.observed <= self.maximum):
            raise ValueError("repeatability check result contradicts its threshold")
        return self


class RunGroupAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: NonEmptyString
    role: Literal["reference", "held_out_repeat"]


RepeatabilityResult.model_rebuild()


class AnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: NonEmptyString
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)


class AnalyzerIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    package_version: NonEmptyString
    git_revision: NonEmptyString | None
    analyzer_revision: Literal["system-identification/v2"]


class IdentificationConclusion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: Literal["pass", "fail", "inconclusive"]
    summary: NonEmptyString
    warnings: tuple[str, ...]
    reference_run_id: NonEmptyString
    held_out_repeat_run_id: NonEmptyString
    group_assignments: tuple[RunGroupAssignment, RunGroupAssignment]
    monotonicity_status: Literal["estimated", "inconclusive"]
    saturation_status: Literal["estimated", "inconclusive"]
    asymmetry_status: Literal["estimated", "inconclusive"]


class IdentificationAnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    analyzer_revision: Literal["system-identification/v2"]
    bootstrap_seed: Literal[20260831]
    bootstrap_replicates: int = Field(gt=0)
    input_config_sha256: tuple[Sha256Hex, ...]
    repeatability_thresholds_sha256: Sha256Hex
    snr_formula_revision: Literal["session-effect-rss-population/v2"]
    snr_formula: NonEmptyString
    hysteresis_definition: NonEmptyString
    variance_definition_revision: Literal["population-ddof0/v1"]
    variance_definition: NonEmptyString
    monotonicity_definition: NonEmptyString
    asymmetry_definition: NonEmptyString
    saturation_definition_revision: Literal["outer-inner-slope-ratio/v1"]
    saturation_definition: NonEmptyString
    reference_run_id: NonEmptyString
    held_out_repeat_run_id: NonEmptyString
    group_assignments: tuple[RunGroupAssignment, RunGroupAssignment]


class IdentificationCompatibilitySignature(BaseModel):
    """Fields that must be identical before sessions may be pooled."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    hardware_id: NonEmptyString
    hardware_manifest_sha256: Sha256Hex
    hardware_manifest_canonical_sha256: Sha256Hex
    calibration_sha256: Sha256Hex
    camera_id: NonEmptyString
    detector: NonEmptyString
    detector_model_sha256: Sha256Hex
    camera_settings: NegotiatedCameraSettings
    observation_schema_version: Literal["blendshape-observation/v1"]
    actuator_names: tuple[NonEmptyString, ...]
    offsets: tuple[Annotated[float, Field(allow_inf_nan=False)], ...]
    samples_per_step: int = Field(gt=0)
    command_interval_ms: int = Field(ge=0)
    controller_settle_ms: int = Field(ge=0)
    visual_settle_ms: int = Field(ge=0)
    sample_interval_ms: int = Field(ge=0)
    step_timeout_ms: int = Field(gt=0)
    command_ttl_ms: int = Field(gt=0)
    maximum_visual_variance: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    home_delta_tolerances: tuple[
        tuple[NonEmptyString, Annotated[float, Field(ge=0, allow_inf_nan=False)]], ...
    ]
    recovery_timeout_ms: int = Field(gt=0)
    maximum_recovery_attempts: int = Field(gt=0)


class IdentificationAnalysisManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["identification-analysis-manifest/v1"]
    generation_id: NonEmptyString
    analysis_kind: Literal["system_identification"]
    generated_at: AwareDatetime
    analyzer: AnalyzerIdentity
    config: IdentificationAnalysisConfig
    config_sha256: Sha256Hex
    bootstrap_seed: Literal[20260831]
    reference_run_id: NonEmptyString
    held_out_repeat_run_id: NonEmptyString
    group_assignments: tuple[RunGroupAssignment, RunGroupAssignment]
    repeatability_thresholds: RepeatabilityThresholds | None
    repeatability_thresholds_sha256: Sha256Hex
    inputs: dict[NonEmptyString, AnalysisInput]
    artifacts: dict[NonEmptyString, ArtifactRecord]
    conclusion: IdentificationConclusion


def estimate_local_jacobian(
    samples: Sequence[IdentificationSample],
) -> IdentificationMetrics:
    """Estimate a named local Jacobian using session-level central differences."""

    if not samples:
        raise ValueError("at least one identification sample is required")
    sessions = tuple(sorted({sample.session_id for sample in samples}))
    actuators = tuple(sorted({sample.actuator_name for sample in samples}))
    blendshapes = tuple(
        sorted({name for sample in samples for name in sample.blendshapes})
    )
    grouped: dict[tuple[str, str, float, str], list[IdentificationSample]] = (
        defaultdict(list)
    )
    for sample in samples:
        grouped[
            (
                sample.session_id,
                sample.actuator_name,
                sample.normalized_position,
                sample.phase,
            )
        ].append(sample)

    session_slopes: dict[tuple[str, str, str], float] = {}
    for session in sessions:
        for actuator in actuators:
            positives = [
                key
                for key in grouped
                if key[0] == session and key[1] == actuator and key[3] == "positive"
            ]
            negatives = [
                key
                for key in grouped
                if key[0] == session and key[1] == actuator and key[3] == "negative"
            ]
            if not positives or not negatives:
                continue
            positive_position = max(key[2] for key in positives)
            negative_position = min(key[2] for key in negatives)
            for blendshape in blendshapes:
                positive_values = _values(
                    grouped[(session, actuator, positive_position, "positive")],
                    blendshape,
                )
                negative_values = _values(
                    grouped[(session, actuator, negative_position, "negative")],
                    blendshape,
                )
                if positive_values and negative_values:
                    session_slopes[(session, blendshape, actuator)] = (
                        float(np.mean(positive_values))
                        - float(np.mean(negative_values))
                    ) / (positive_position - negative_position)

    jacobian_cells: list[MatrixCell] = []
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    for blendshape in blendshapes:
        for actuator in actuators:
            estimates = [
                session_slopes[(session, blendshape, actuator)]
                for session in sessions
                if (session, blendshape, actuator) in session_slopes
            ]
            if not estimates:
                jacobian_cells.append(
                    _missing_cell(
                        blendshape,
                        actuator,
                        "positive and negative samples are required",
                    )
                )
                continue
            value = float(np.mean(estimates))
            if len(estimates) < 2:
                jacobian_cells.append(
                    _estimated_cell(
                        blendshape,
                        actuator,
                        value,
                        uncertainty_reason=(
                            "bootstrap confidence interval requires at least "
                            "two sessions"
                        ),
                    )
                )
            else:
                array = np.asarray(estimates, dtype=float)
                indices = rng.integers(
                    0, len(array), size=(BOOTSTRAP_REPLICATES, len(array))
                )
                draws = np.mean(array[indices], axis=1)
                low, high = np.quantile(draws, (0.025, 0.975))
                jacobian_cells.append(
                    _estimated_cell(
                        blendshape,
                        actuator,
                        value,
                        confidence=(float(low), float(high)),
                    )
                )
    jacobian = NamedMatrix(
        row_labels=blendshapes, column_labels=actuators, cells=tuple(jacobian_cells)
    )

    position_columns = tuple(
        f"{actuator}:{position:+.6g}"
        for actuator in actuators
        for position in sorted(
            {
                s.normalized_position
                for s in samples
                if s.actuator_name == actuator and s.phase != "home"
            }
        )
    )
    position_cells: list[MatrixCell] = []
    between_position_cells: list[MatrixCell] = []
    for blendshape in blendshapes:
        for column in position_columns:
            actuator, encoded = column.split(":", 1)
            within, between = _variance_components(
                samples,
                blendshape,
                lambda sample: (
                    sample.actuator_name == actuator
                    and sample.normalized_position == float(encoded)
                    and sample.phase != "home"
                ),
            )
            position_cells.append(_metric_cell(blendshape, column, within))
            between_position_cells.append(_metric_cell(blendshape, column, between))
    position_variance = NamedMatrix(
        row_labels=blendshapes,
        column_labels=position_columns,
        cells=tuple(position_cells),
    )
    between_position_variance = NamedMatrix(
        row_labels=blendshapes,
        column_labels=position_columns,
        cells=tuple(between_position_cells),
    )

    baseline_cells: list[MatrixCell] = []
    between_baseline_cells: list[MatrixCell] = []
    for blendshape in blendshapes:
        for actuator in actuators:
            within, between = _variance_components(
                samples,
                blendshape,
                lambda sample: (
                    sample.actuator_name == actuator and sample.phase == "home"
                ),
            )
            baseline_cells.append(_metric_cell(blendshape, actuator, within))
            between_baseline_cells.append(_metric_cell(blendshape, actuator, between))
    baseline_variance = NamedMatrix(
        row_labels=blendshapes, column_labels=actuators, cells=tuple(baseline_cells)
    )
    between_baseline_variance = NamedMatrix(
        row_labels=blendshapes,
        column_labels=actuators,
        cells=tuple(between_baseline_cells),
    )

    snr_cells: list[MatrixCell] = []
    noise_cells: list[MatrixCell] = []
    hysteresis_cells: list[MatrixCell] = []
    home_drift_cells: list[MatrixCell] = []
    for blendshape in blendshapes:
        for actuator in actuators:
            signal, noise = _effect_and_noise(samples, actuator, blendshape)
            if noise is None:
                noise_cells.append(
                    _missing_cell(
                        blendshape,
                        actuator,
                        (
                            "within-session-position noise requires positive "
                            "residual degrees of freedom"
                        ),
                    )
                )
            else:
                noise_cells.append(
                    _estimated_cell(
                        blendshape,
                        actuator,
                        noise,
                        uncertainty_reason=(
                            "pooled noise confidence interval is not estimated"
                        ),
                    )
                )
            if signal is None or noise is None:
                snr_cells.append(
                    _missing_cell(
                        blendshape,
                        actuator,
                        "effect or repeated-position variance is unavailable",
                    )
                )
            elif noise == 0:
                snr_cells.append(
                    _undefined_cell(
                        blendshape,
                        actuator,
                        "signal-to-noise is undefined for zero measured noise",
                    )
                )
            else:
                snr_cells.append(
                    _estimated_cell(
                        blendshape,
                        actuator,
                        signal / noise,
                        uncertainty_reason="SNR confidence interval is not estimated",
                    )
                )
            deltas = _hysteresis_deltas(samples, actuator, blendshape)
            hysteresis_cells.append(
                _mean_cell(
                    blendshape,
                    actuator,
                    deltas,
                    (
                        "ordered home observations before and after excursions "
                        "are required"
                    ),
                )
            )
            home_drift_cells.append(
                _mean_cell(
                    blendshape,
                    actuator,
                    _return_home_deltas(samples, actuator, blendshape),
                    "initial and final Home observations are required",
                )
            )
    snr = NamedMatrix(
        row_labels=blendshapes, column_labels=actuators, cells=tuple(snr_cells)
    )
    noise_sd = NamedMatrix(
        row_labels=blendshapes, column_labels=actuators, cells=tuple(noise_cells)
    )
    hysteresis = NamedMatrix(
        row_labels=blendshapes, column_labels=actuators, cells=tuple(hysteresis_cells)
    )
    home_drift = NamedMatrix(
        row_labels=blendshapes, column_labels=actuators, cells=tuple(home_drift_cells)
    )

    warnings: list[str] = []
    if any(cell.value is None for cell in jacobian.cells):
        matrix: NDArray[np.float64] | None = None
        singular: NDArray[np.float64] = np.asarray([], dtype=np.float64)
        rank = None
        warnings.append(
            "rank and observability are unavailable for an incomplete Jacobian"
        )
        condition = _missing_metric(
            "condition number requires a complete local Jacobian"
        )
    else:
        matrix_values: list[list[float]] = [
            [_required_value(_find_cell(jacobian, row, column)) for column in actuators]
            for row in blendshapes
        ]
        matrix = np.asarray(matrix_values, dtype=np.float64)
        singular = np.linalg.svd(matrix, compute_uv=False)
        tolerance = (
            max(matrix.shape)
            * (singular[0] if len(singular) else 0.0)
            * np.finfo(float).eps
        )
        rank = int(np.sum(singular > tolerance))
        unobservable = [
            row
            for i, row in enumerate(blendshapes)
            if np.linalg.norm(matrix[i, :]) <= tolerance
        ]
        if unobservable:
            warnings.append(
                "unobservable blendshape dimensions: " + ", ".join(unobservable)
            )
        if rank < min(matrix.shape):
            warnings.append(
                f"rank deficient local Jacobian: rank {rank} of {min(matrix.shape)}"
            )
            condition = _undefined_metric(
                "condition number is undefined for a rank-deficient Jacobian"
            )
        else:
            condition = _estimated_metric(
                float(singular[0] / singular[-1]),
                "condition-number confidence interval is not estimated",
            )

    coupling_cells: list[MatrixCell] = []
    for left in actuators:
        for right in actuators:
            if matrix is None:
                coupling_cells.append(
                    _missing_cell(
                        left,
                        right,
                        "coupling requires a complete local Jacobian",
                    )
                )
                continue
            a = matrix[:, actuators.index(left)]
            b = matrix[:, actuators.index(right)]
            denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
            if denominator == 0:
                coupling_cells.append(
                    _undefined_cell(
                        left, right, "coupling is undefined for a zero effect vector"
                    )
                )
            else:
                coupling_cells.append(
                    _estimated_cell(
                        left,
                        right,
                        float(np.dot(a, b) / denominator),
                        uncertainty_reason=(
                            "coupling confidence interval is not estimated"
                        ),
                    )
                )
    coupling = NamedMatrix(
        row_labels=actuators, column_labels=actuators, cells=tuple(coupling_cells)
    )

    monotonicity_cells: list[MatrixCell] = []
    asymmetry_cells: list[MatrixCell] = []
    saturation_cells: list[MatrixCell] = []
    for blendshape in blendshapes:
        for actuator in actuators:
            selected = [
                sample for sample in samples if sample.actuator_name == actuator
            ]
            by_position: dict[float, list[float]] = defaultdict(list)
            for sample in selected:
                if blendshape in sample.blendshapes:
                    by_position[sample.normalized_position].append(
                        float(sample.blendshapes[blendshape])
                    )
            means_by_position = {
                position: float(np.mean(values))
                for position, values in by_position.items()
            }
            home = means_by_position.get(0.0)
            positive_positions = sorted(
                position for position in means_by_position if position > 0
            )
            negative_positions = sorted(
                (position for position in means_by_position if position < 0),
                key=abs,
            )
            if home is None or not positive_positions or not negative_positions:
                reason = "Home and both signed offsets are required"
                monotonicity_cells.append(_missing_cell(blendshape, actuator, reason))
                asymmetry_cells.append(_missing_cell(blendshape, actuator, reason))
            else:
                positive_slope = (
                    means_by_position[positive_positions[0]] - home
                ) / positive_positions[0]
                negative_slope = (
                    means_by_position[negative_positions[0]] - home
                ) / negative_positions[0]
                expected_sign = np.sign(positive_slope + negative_slope)
                agreements = (
                    float(
                        np.sign(means_by_position[positive_positions[0]] - home)
                        == expected_sign
                    ),
                    float(
                        np.sign(means_by_position[negative_positions[0]] - home)
                        == -expected_sign
                    ),
                )
                monotonicity_cells.append(
                    _estimated_cell(
                        blendshape,
                        actuator,
                        float(np.mean(agreements)),
                        uncertainty_reason=(
                            "signed-consistency uncertainty is unavailable"
                        ),
                    )
                )
                asymmetry_cells.append(
                    _estimated_cell(
                        blendshape,
                        actuator,
                        abs(abs(positive_slope) - abs(negative_slope)),
                        uncertainty_reason=(
                            "one-sided slope asymmetry uncertainty is unavailable"
                        ),
                    )
                )
            if (
                home is None
                or len(positive_positions) < 2
                or len(negative_positions) < 2
            ):
                saturation_cells.append(
                    _missing_cell(
                        blendshape,
                        actuator,
                        "multiple nonzero magnitudes on both sides are required "
                        "for saturation",
                    )
                )
            else:
                ratios: list[float] = []
                for positions in (positive_positions, negative_positions):
                    inner, outer = positions[0], positions[1]
                    inner_slope = (means_by_position[inner] - home) / inner
                    outer_incremental = (
                        means_by_position[outer] - means_by_position[inner]
                    ) / (outer - inner)
                    if inner_slope != 0:
                        ratios.append(1.0 - abs(outer_incremental) / abs(inner_slope))
                saturation_cells.append(
                    _estimated_cell(
                        blendshape,
                        actuator,
                        float(np.mean(ratios)),
                        uncertainty_reason="saturation uncertainty is unavailable",
                    )
                    if ratios
                    else _undefined_cell(
                        blendshape, actuator, "inner slope is zero on both sides"
                    )
                )
    monotonicity = NamedMatrix(
        row_labels=blendshapes,
        column_labels=actuators,
        cells=tuple(monotonicity_cells),
    )
    asymmetry = NamedMatrix(
        row_labels=blendshapes,
        column_labels=actuators,
        cells=tuple(asymmetry_cells),
    )
    saturation = NamedMatrix(
        row_labels=blendshapes,
        column_labels=actuators,
        cells=tuple(saturation_cells),
    )

    return IdentificationMetrics(
        schema_version="identification-metrics/v2",
        bootstrap_seed=BOOTSTRAP_SEED,
        bootstrap_replicates=BOOTSTRAP_REPLICATES,
        session_ids=sessions,
        variance_definition_revision=VARIANCE_DEFINITION_REVISION,
        variance_definition=VARIANCE_DEFINITION,
        jacobian=jacobian,
        baseline_variance=baseline_variance,
        between_session_baseline_variance=between_baseline_variance,
        position_variance=position_variance,
        between_session_position_variance=between_position_variance,
        within_position_noise_sd=noise_sd,
        signal_to_noise=snr,
        snr_formula_revision=SNR_FORMULA_REVISION,
        snr_formula=SNR_FORMULA,
        hysteresis=hysteresis,
        hysteresis_definition=HYSTERESIS_DEFINITION,
        monotonicity=monotonicity,
        monotonicity_definition=MONOTONICITY_DEFINITION,
        asymmetry=asymmetry,
        asymmetry_definition=ASYMMETRY_DEFINITION,
        saturation=saturation,
        saturation_definition_revision=SATURATION_DEFINITION_REVISION,
        saturation_definition=SATURATION_DEFINITION,
        return_to_home_drift=home_drift,
        cross_effects=jacobian,
        actuator_coupling=coupling,
        controller_settling_ms=_duration_metric(samples, "controller"),
        command_to_first_visual_ms=_duration_metric(samples, "first_visual"),
        visual_window_settling_ms=_duration_metric(samples, "visual_window"),
        total_command_to_visual_settled_ms=_duration_metric(samples, "total_visual"),
        rank=rank,
        singular_values=tuple(float(value) for value in singular),
        condition_number=condition,
        warnings=tuple(warnings),
    )


def compare_repeat_run(
    reference: IdentificationMetrics,
    repeat: IdentificationMetrics,
    thresholds: RepeatabilityThresholds | None = None,
) -> RepeatabilityResult:
    if len(reference.session_ids) != 1 or len(repeat.session_ids) != 1:
        raise ValueError(
            "repeatability requires exactly one reference run and one held-out "
            "repeat run"
        )
    reference_run_id = reference.session_ids[0]
    repeat_run_id = repeat.session_ids[0]
    if reference_run_id == repeat_run_id:
        raise ValueError("reference and held-out repeat run IDs must be distinct")
    group_assignments = (
        RunGroupAssignment(run_id=reference_run_id, role="reference"),
        RunGroupAssignment(run_id=repeat_run_id, role="held_out_repeat"),
    )
    rows = tuple(
        dict.fromkeys((*reference.jacobian.row_labels, *repeat.jacobian.row_labels))
    )
    columns = tuple(
        dict.fromkeys(
            (*reference.jacobian.column_labels, *repeat.jacobian.column_labels)
        )
    )
    cells: list[MatrixCell] = []
    warnings: list[str] = []
    incomplete = False
    for row in rows:
        for column in columns:
            left = _find_cell_optional(reference.jacobian, row, column)
            right = _find_cell_optional(repeat.jacobian, row, column)
            if (
                left is None
                or right is None
                or left.value is None
                or right.value is None
            ):
                incomplete = True
                cells.append(
                    _missing_cell(
                        row, column, "effect is absent from reference or repeat run"
                    )
                )
            else:
                cells.append(
                    _estimated_cell(
                        row,
                        column,
                        abs(right.value - left.value),
                        uncertainty_reason=(
                            "repeat-delta confidence interval is not estimated"
                        ),
                    )
                )
    if incomplete:
        warnings.append("repeat comparison has missing actuator/blendshape effects")
    checks: list[RepeatabilityCheck] = []
    if thresholds is not None:
        for threshold in thresholds.cells:
            cell = _find_cell_optional(
                NamedMatrix(row_labels=rows, column_labels=columns, cells=tuple(cells)),
                threshold.blendshape_name,
                threshold.actuator_name,
            )
            observed = None if cell is None else cell.value
            checks.append(
                RepeatabilityCheck(
                    check_id=(
                        f"jacobian:{threshold.blendshape_name}:"
                        f"{threshold.actuator_name}"
                    ),
                    observed=observed,
                    maximum=threshold.maximum_absolute_delta,
                    passed=(
                        None
                        if observed is None
                        else observed <= threshold.maximum_absolute_delta
                    ),
                    reason=(
                        "named Jacobian effect is unavailable"
                        if observed is None
                        else None
                    ),
                )
            )
        if thresholds.maximum_mean_absolute_delta is not None:
            available = [cell.value for cell in cells if cell.value is not None]
            observed_mean = (
                float(np.mean(available))
                if len(available) == len(cells) and available
                else None
            )
            checks.append(
                RepeatabilityCheck(
                    check_id="jacobian:mean_absolute_delta",
                    observed=observed_mean,
                    maximum=thresholds.maximum_mean_absolute_delta,
                    passed=(
                        None
                        if observed_mean is None
                        else observed_mean <= thresholds.maximum_mean_absolute_delta
                    ),
                    reason=(
                        "aggregate requires every named Jacobian effect"
                        if observed_mean is None
                        else None
                    ),
                )
            )
    if any(check.passed is False for check in checks):
        outcome: Literal["pass", "fail", "inconclusive"] = "fail"
    elif (
        thresholds is None
        or not checks
        or any(check.passed is None for check in checks)
    ):
        outcome = "inconclusive"
    else:
        outcome = "pass"
    return RepeatabilityResult(
        schema_version="identification-repeatability/v1",
        reference_seed=reference.bootstrap_seed,
        repeat_seed=repeat.bootstrap_seed,
        reference_run_id=reference_run_id,
        repeat_run_id=repeat_run_id,
        group_assignments=group_assignments,
        absolute_delta=NamedMatrix(
            row_labels=rows, column_labels=columns, cells=tuple(cells)
        ),
        checks=tuple(checks),
        outcome=outcome,
        warnings=tuple(warnings),
    )


def analyze_identification_artifacts(run_dirs: Sequence[Path]) -> IdentificationMetrics:
    samples: list[IdentificationSample] = []
    seen: set[str] = set()
    signature: IdentificationCompatibilitySignature | None = None
    for run_dir in run_dirs:
        _, manifest, artifacts = _verified_run(run_dir)
        if manifest.run_id in seen:
            raise ValueError(f"duplicate run_id: {manifest.run_id}")
        seen.add(manifest.run_id)
        current_signature = _compatibility_signature(manifest)
        if signature is None:
            signature = current_signature
        elif current_signature != signature:
            raise ValueError("identification compatibility signature mismatch")
        samples.extend(_samples_from_artifacts(manifest, artifacts))
    return estimate_local_jacobian(samples)


def publish_identification_analysis(
    *,
    reference_run_dir: Path,
    held_out_repeat_run_dir: Path,
    output_dir: Path,
    generation_id: str | None = None,
    repeatability_thresholds: RepeatabilityThresholds | None = None,
) -> tuple[IdentificationAnalysisManifest, Path]:
    reference = (
        reference_run_dir.resolve(),
        *_verified_run(reference_run_dir),
    )
    repeat = (
        held_out_repeat_run_dir.resolve(),
        *_verified_run(held_out_repeat_run_dir),
    )
    reference_manifest = reference[2]
    repeat_manifest = repeat[2]
    if reference_manifest.run_id == repeat_manifest.run_id:
        raise ValueError("reference and held-out repeat run IDs must be distinct")
    if _compatibility_signature(reference_manifest) != _compatibility_signature(
        repeat_manifest
    ):
        raise ValueError("identification compatibility signature mismatch")
    reference_samples = _samples_from_artifacts(reference_manifest, reference[3])
    repeat_samples = _samples_from_artifacts(repeat_manifest, repeat[3])
    metrics = estimate_local_jacobian([*reference_samples, *repeat_samples])
    repeatability = compare_repeat_run(
        estimate_local_jacobian(reference_samples),
        estimate_local_jacobian(repeat_samples),
        repeatability_thresholds,
    )
    outcome = repeatability.outcome
    conclusion_warnings = tuple(
        dict.fromkeys((*metrics.warnings, *repeatability.warnings))
    )
    conclusion = IdentificationConclusion(
        outcome=outcome,
        summary=(
            "System-identification metrics and the configured repeatability gate "
            f"for reference {reference_manifest.run_id} and held-out repeat "
            f"{repeat_manifest.run_id} produced outcome {outcome}."
        ),
        warnings=conclusion_warnings,
        reference_run_id=reference_manifest.run_id,
        held_out_repeat_run_id=repeat_manifest.run_id,
        group_assignments=repeatability.group_assignments,
        monotonicity_status=(
            "estimated"
            if any(cell.status == "estimated" for cell in metrics.monotonicity.cells)
            else "inconclusive"
        ),
        saturation_status=(
            "estimated"
            if any(cell.status == "estimated" for cell in metrics.saturation.cells)
            else "inconclusive"
        ),
        asymmetry_status=(
            "estimated"
            if any(cell.status == "estimated" for cell in metrics.asymmetry.cells)
            else "inconclusive"
        ),
    )
    template = (
        files("alice.resources")
        .joinpath("actuator-identification-conclusion.md")
        .read_text(encoding="utf-8")
    )
    report = template.format(
        outcome=conclusion.outcome,
        summary=conclusion.summary,
        warnings="\n".join(f"- {item}" for item in conclusion.warnings) or "- None",
        monotonicity_status=conclusion.monotonicity_status,
        saturation_status=conclusion.saturation_status,
        asymmetry_status=conclusion.asymmetry_status,
    )
    payloads = {
        "identification-metrics.json": _pretty(metrics.model_dump(mode="json")),
        "conclusion.json": _pretty(conclusion.model_dump(mode="json")),
        "actuator-identification-conclusion.md": report.encode(),
    }
    payloads["repeatability.json"] = _pretty(repeatability.model_dump(mode="json"))
    inputs: dict[str, AnalysisInput] = {}
    config_hashes: list[str] = []
    for _, raw_manifest, manifest, artifacts in (reference, repeat):
        metadata = manifest.identification_metadata
        if metadata is None:  # enforced by ArtifactManifest, retained for typing
            raise ValueError("identification metadata is required")
        config_hashes.append(metadata.config_sha256)
        inputs[f"{manifest.run_id}.manifest"] = _input("manifest.json", raw_manifest)
        for name, payload in artifacts.items():
            inputs[f"{manifest.run_id}.{name}"] = _input(name, payload)
    threshold_payload = _canonical_json(
        None
        if repeatability_thresholds is None
        else repeatability_thresholds.model_dump(mode="json")
    )
    threshold_sha256 = hashlib.sha256(threshold_payload).hexdigest()
    analysis_config = IdentificationAnalysisConfig(
        analyzer_revision=ANALYZER_REVISION,
        bootstrap_seed=BOOTSTRAP_SEED,
        bootstrap_replicates=BOOTSTRAP_REPLICATES,
        input_config_sha256=tuple(config_hashes),
        repeatability_thresholds_sha256=threshold_sha256,
        snr_formula_revision=SNR_FORMULA_REVISION,
        snr_formula=SNR_FORMULA,
        hysteresis_definition=HYSTERESIS_DEFINITION,
        variance_definition_revision=VARIANCE_DEFINITION_REVISION,
        variance_definition=VARIANCE_DEFINITION,
        monotonicity_definition=MONOTONICITY_DEFINITION,
        asymmetry_definition=ASYMMETRY_DEFINITION,
        saturation_definition_revision=SATURATION_DEFINITION_REVISION,
        saturation_definition=SATURATION_DEFINITION,
        reference_run_id=reference_manifest.run_id,
        held_out_repeat_run_id=repeat_manifest.run_id,
        group_assignments=repeatability.group_assignments,
    )
    config_payload = json.dumps(
        analysis_config.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    config_sha = hashlib.sha256(config_payload).hexdigest()
    now = datetime.now(UTC)
    identity = generation_id or f"identification-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    analysis_manifest = IdentificationAnalysisManifest(
        schema_version="identification-analysis-manifest/v1",
        generation_id=identity,
        analysis_kind="system_identification",
        generated_at=now,
        analyzer=AnalyzerIdentity(
            package_version=_package_version(),
            git_revision=_git_revision(),
            analyzer_revision=ANALYZER_REVISION,
        ),
        config=analysis_config,
        config_sha256=config_sha,
        bootstrap_seed=BOOTSTRAP_SEED,
        reference_run_id=reference_manifest.run_id,
        held_out_repeat_run_id=repeat_manifest.run_id,
        group_assignments=repeatability.group_assignments,
        repeatability_thresholds=repeatability_thresholds,
        repeatability_thresholds_sha256=threshold_sha256,
        inputs=inputs,
        artifacts={name: _record(name, payload) for name, payload in payloads.items()},
        conclusion=conclusion,
    )
    payloads["analysis-manifest.json"] = _pretty(
        analysis_manifest.model_dump(mode="json")
    )
    generation = publish_generation(
        output_dir.resolve() / "analysis" / "generations", identity, payloads
    )
    return analysis_manifest, generation


def _verified_run(
    run_dir: Path,
) -> tuple[bytes, ArtifactManifest, dict[str, bytes]]:
    root = run_dir.resolve()
    raw = (root / "manifest.json").read_bytes()
    manifest = ArtifactManifest.model_validate_json(raw)
    if manifest.run_kind is not RunKind.ACTUATOR_IDENTIFICATION:
        raise ValueError("analysis requires actuator_identification run kind")
    if manifest.status is not RunStatus.COMPLETED:
        raise ValueError("analysis requires a completed run")
    metadata = manifest.identification_metadata
    if metadata is None:
        raise ValueError("identification provenance is missing")
    if metadata.observer is None or metadata.observer != metadata.expected_observer:
        raise ValueError("runtime observer provenance is missing or mismatched")
    try:
        configured_observer = IdentificationObserverProvenance.model_validate(
            manifest.config["observer"]
        )
    except (KeyError, ValueError) as exc:
        raise ValueError(
            "configured observer provenance is incomplete or invalid"
        ) from exc
    if (
        configured_observer != metadata.expected_observer
        or configured_observer != metadata.observer
        or manifest.camera_settings != metadata.observer.camera_settings
    ):
        raise ValueError(
            "configured, expected, and runtime observer provenance mismatch"
        )
    try:
        config_identity_matches = (
            manifest.config["hardware_manifest_sha256"]
            == metadata.hardware_manifest_sha256
            and manifest.config["hardware_manifest_canonical_sha256"]
            == metadata.hardware_manifest_canonical_sha256
            and manifest.config["calibration_sha256"] == metadata.calibration_sha256
        )
    except KeyError as exc:
        raise ValueError("hardware/config provenance is incomplete") from exc
    if not config_identity_matches:
        raise ValueError("hardware/config provenance identity mismatch")
    config_payload = json.dumps(
        manifest.config, sort_keys=True, separators=(",", ":")
    ).encode()
    if hashlib.sha256(config_payload).hexdigest() != metadata.config_sha256:
        raise ValueError("config checksum does not match identification provenance")
    required = (
        "commands.jsonl",
        "statuses.jsonl",
        "observations.jsonl",
        "controller-settling.jsonl",
        "visual-settling.jsonl",
    )
    for name in required:
        if name not in manifest.artifacts:
            raise ValueError(f"required artifact missing: {name}")
    artifacts: dict[str, bytes] = {}
    for name, record in manifest.artifacts.items():
        if record.path != name:
            raise ValueError("artifact key/path mismatch")
        path = (root / name).resolve()
        if path.parent != root:
            raise ValueError("artifact path escapes run directory")
        payload = path.read_bytes()
        if len(payload) != record.size_bytes:
            raise ValueError(f"artifact size mismatch: {name}")
        if hashlib.sha256(payload).hexdigest() != record.sha256:
            raise ValueError(f"artifact checksum mismatch: {name}")
        artifacts[name] = payload
    return raw, manifest, artifacts


def _compatibility_signature(
    manifest: ArtifactManifest,
) -> IdentificationCompatibilitySignature:
    metadata = manifest.identification_metadata
    if metadata is None or metadata.observer is None:
        raise ValueError("identification provenance is missing")
    config = manifest.config
    try:
        tolerances = tuple(
            sorted(
                (str(name), float(value))
                for name, value in config["home_delta_tolerances"].items()
            )
        )
        return IdentificationCompatibilitySignature(
            hardware_id=config["hardware_id"],
            hardware_manifest_sha256=metadata.hardware_manifest_sha256,
            hardware_manifest_canonical_sha256=(
                metadata.hardware_manifest_canonical_sha256
            ),
            calibration_sha256=metadata.calibration_sha256,
            camera_id=metadata.observer.camera_id,
            detector=metadata.observer.detector,
            detector_model_sha256=metadata.observer.detector_model_sha256,
            camera_settings=metadata.observer.camera_settings,
            observation_schema_version="blendshape-observation/v1",
            actuator_names=tuple(config["actuator_names"]),
            offsets=tuple(config["offsets"]),
            samples_per_step=config["samples_per_step"],
            command_interval_ms=config["command_interval_ms"],
            controller_settle_ms=config["controller_settle_ms"],
            visual_settle_ms=config["visual_settle_ms"],
            sample_interval_ms=config["sample_interval_ms"],
            step_timeout_ms=config["step_timeout_ms"],
            command_ttl_ms=config["command_ttl_ms"],
            maximum_visual_variance=config["maximum_visual_variance"],
            home_delta_tolerances=tolerances,
            recovery_timeout_ms=config["recovery_timeout_ms"],
            maximum_recovery_attempts=config["maximum_recovery_attempts"],
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "identification compatibility signature fields are missing or invalid"
        ) from exc


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _samples_from_artifacts(
    manifest: ArtifactManifest, artifacts: Mapping[str, bytes]
) -> list[IdentificationSample]:
    metadata = manifest.identification_metadata
    if metadata is None or metadata.observer is None:
        raise ValueError("identification observer provenance is missing")
    commands = _normal_commands(manifest, artifacts["commands.jsonl"])
    statuses = _status_evidence(manifest, artifacts["statuses.jsonl"])
    controller = _settling_decisions(
        manifest,
        artifacts["controller-settling.jsonl"],
        ControllerSettlingDecision,
    )
    visual = _settling_decisions(
        manifest,
        artifacts["visual-settling.jsonl"],
        VisualSettlingDecision,
    )
    observations = _jsonl(artifacts["observations.jsonl"])
    if len(observations) != manifest.observation_count:
        raise ValueError("observation_count does not match observations artifact")
    order = {step: index for index, step in enumerate(commands, 1)}
    expected_steps = set(commands)
    if (
        set(statuses) != expected_steps
        or set(controller) != expected_steps
        or set(visual) != expected_steps
    ):
        raise ValueError("command/status/settling step coverage is not one-to-one")
    by_step: dict[str, list[tuple[int, BlendshapeObservation]]] = defaultdict(list)
    observer = metadata.observer
    category_names: tuple[str, ...] | None = None
    last_visual_decided = -1
    result: list[IdentificationSample] = []
    for item in observations:
        if item.get("run_id") != manifest.run_id:
            raise ValueError("observation record run_id mismatch")
        step_id = str(item["step_id"])
        if (
            step_id not in commands
            or step_id not in controller
            or step_id not in visual
        ):
            raise ValueError("observation references incomplete step evidence")
        command = commands[step_id]
        try:
            observation = BlendshapeObservation.model_validate(item["observation"])
        except (KeyError, ValueError) as exc:
            raise ValueError(
                "observation artifact contains invalid typed data"
            ) from exc
        if (
            observation.run_id != manifest.run_id
            or observation.validity != "valid"
            or observation.camera_id != observer.camera_id
            or observation.detector != observer.detector
            or observation.detector_model_sha256 != observer.detector_model_sha256
        ):
            raise ValueError(
                "identification observation identity is invalid or mismatched"
            )
        names = tuple(score.name for score in observation.scores)
        if category_names is None:
            category_names = names
        elif names != category_names:
            raise ValueError("observation category identity/order mismatch")
        sample_index = item.get("sample_index")
        if not isinstance(sample_index, int) or sample_index < 0:
            raise ValueError("observation sample order is invalid")
        by_step[step_id].append((sample_index, observation))
        scores = {score.name: score.score for score in observation.scores}
        controller_decision = controller[step_id]
        visual_decision = visual[step_id]
        status_evidence = statuses[step_id]
        if not isinstance(
            controller_decision, ControllerSettlingDecision
        ) or not isinstance(visual_decision, VisualSettlingDecision):
            raise ValueError("settling artifact decision types are inconsistent")
        if (
            status_evidence.status.request_id != command.request.request_id
            or status_evidence.status.applied_targets != command.request.targets
            or status_evidence.status.state is not ActuatorStatusState.APPLIED
            or status_evidence.status.targets_reached is not True
        ):
            raise ValueError("status does not correlate with authorized command target")
        if (
            controller_decision.requested_targets != command.request.targets
            or controller_decision.target_reached is not True
            or not controller_decision.samples
            or controller_decision.samples
            != status_evidence.status.controller_output_samples
        ):
            raise ValueError(
                "controller settling does not correlate with status/target"
            )
        if visual_decision.variance_accepted is not True or (
            command.step.phase == "home"
            and (
                visual_decision.home_verified is not True
                or visual_decision.baseline_accepted is not True
            )
        ):
            raise ValueError("visual settling evidence was not accepted")
        if sample_index == 0:
            if command.monotonic_ns <= last_visual_decided:
                raise ValueError("command/visual step order is invalid")
            last_visual_decided = visual_decision.decided_monotonic_ns
        if (
            status_evidence.monotonic_ns < command.monotonic_ns
            or controller_decision.decided_monotonic_ns < status_evidence.monotonic_ns
            or observation.monotonic_ns < controller_decision.decided_monotonic_ns
            or visual_decision.decided_monotonic_ns < observation.monotonic_ns
        ):
            raise ValueError("cross-artifact monotonic order is invalid")
        result.append(
            IdentificationSample(
                session_id=manifest.run_id,
                step_id=step_id,
                sequence_index=order[step_id],
                actuator_name=command.step.actuator_name,
                normalized_position=command.step.normalized_position,
                phase=command.step.phase,
                blendshapes=scores,
                controller_command_ns=command.monotonic_ns,
                controller_settled_ns=max(
                    sample.observed_monotonic_ns
                    for sample in controller_decision.samples
                ),
                first_visual_sample_ns=observation.monotonic_ns,
                visual_settled_ns=visual_decision.decided_monotonic_ns,
            )
        )
    if set(by_step) != expected_steps:
        raise ValueError("observation step coverage is not one-to-one")
    expected_count = int(manifest.config["samples_per_step"])
    for step_id, step_observations in by_step.items():
        indices = [index for index, _ in step_observations]
        times = [observation.monotonic_ns for _, observation in step_observations]
        if indices != list(range(expected_count)) or any(
            right <= left for left, right in zip(times, times[1:], strict=False)
        ):
            raise ValueError(f"observation sample order is invalid for {step_id}")
        decision = visual[step_id]
        if not isinstance(decision, VisualSettlingDecision):
            raise ValueError("visual settling decision type is inconsistent")
        observed_by_name: dict[str, list[float]] = defaultdict(list)
        for _, observation in step_observations:
            for score in observation.scores:
                observed_by_name[score.name].append(score.score)
        recorded_means = {item.name: item.value for item in decision.means}
        recorded_variances = {item.name: item.value for item in decision.variances}
        if set(recorded_means) != set(observed_by_name) or set(
            recorded_variances
        ) != set(observed_by_name):
            raise ValueError(
                "visual settling labels do not correlate with observations"
            )
        for name, values in observed_by_name.items():
            expected_mean = float(np.mean(values))
            expected_variance = float(np.var(values, ddof=0))
            if not math.isclose(
                recorded_means[name], expected_mean, abs_tol=1e-12
            ) or not math.isclose(
                recorded_variances[name], expected_variance, abs_tol=1e-12
            ):
                raise ValueError(
                    "visual settling metrics do not correlate with observations"
                )
    return result


def _normal_commands(
    manifest: ArtifactManifest, payload: bytes
) -> dict[str, _CommandEvidence]:
    result: dict[str, _CommandEvidence] = {}
    previous_monotonic_ns = -1
    for item in _jsonl(payload):
        if item.get("authorization_kind") != "normal":
            continue
        if item.get("run_id") != manifest.run_id:
            raise ValueError("command record run_id mismatch")
        try:
            step = IdentificationStep.model_validate(
                {
                    "step_id": item["step_id"],
                    "actuator_name": item["actuator_name"],
                    "normalized_position": item["normalized_position"],
                    "phase": item["phase"],
                }
            )
            request = PoseRequest.model_validate(item["request"])
            monotonic_ns = int(item["monotonic_ns"])
        except (KeyError, ValueError) as exc:
            raise ValueError("command artifact contains invalid typed data") from exc
        if step.step_id in result:
            raise ValueError("command artifact contains duplicate normal step_id")
        expected_target = (
            ActuatorTarget(
                actuator_name=step.actuator_name,
                normalized_position=step.normalized_position,
            ),
        )
        metadata = manifest.identification_metadata
        assert metadata is not None
        if (
            request.run_id != manifest.run_id
            or request.hardware_id != manifest.config["hardware_id"]
            or request.calibration_sha256 != metadata.calibration_sha256
            or request.targets != expected_target
            or not (
                request.issued_monotonic_ns
                <= monotonic_ns
                < request.expires_monotonic_ns
            )
        ):
            raise ValueError("command/request identity or target correlation mismatch")
        if monotonic_ns <= previous_monotonic_ns:
            raise ValueError("command monotonic order is invalid")
        previous_monotonic_ns = monotonic_ns
        result[step.step_id] = _CommandEvidence(
            step=step, monotonic_ns=monotonic_ns, request=request
        )
    expected: list[tuple[str, float, str]] = []
    positive, negative = manifest.config["offsets"]
    for actuator in manifest.config["actuator_names"]:
        expected.extend(
            (
                (actuator, 0.0, "home"),
                (actuator, positive, "positive"),
                (actuator, 0.0, "home"),
                (actuator, negative, "negative"),
                (actuator, 0.0, "home"),
            )
        )
    actual = [
        (item.step.actuator_name, item.step.normalized_position, item.step.phase)
        for item in result.values()
    ]
    if actual != expected:
        raise ValueError("command target sequence does not match reviewed config")
    return result


def _status_evidence(
    manifest: ArtifactManifest, payload: bytes
) -> dict[str, _StatusEvidence]:
    metadata = manifest.identification_metadata
    if metadata is None:
        raise ValueError("identification provenance is missing")
    result: dict[str, _StatusEvidence] = {}
    for item in _jsonl(payload):
        if item.get("run_id") != manifest.run_id:
            raise ValueError("status record run identity mismatch")
        step_id = str(item.get("step_id", ""))
        if not step_id or step_id in result:
            raise ValueError("status artifact has missing or duplicate step identity")
        try:
            status = ActuatorStatus.model_validate(item["status"])
            monotonic_ns = int(item["monotonic_ns"])
        except (KeyError, ValueError) as exc:
            raise ValueError("status artifact contains invalid typed data") from exc
        if (
            status.run_id != manifest.run_id
            or status.hardware_id != manifest.config["hardware_id"]
            or status.calibration_sha256 != metadata.calibration_sha256
            or status.reported_monotonic_ns > monotonic_ns
        ):
            raise ValueError("status identity or monotonic correlation mismatch")
        result[step_id] = _StatusEvidence(monotonic_ns=monotonic_ns, status=status)
    return result


def _settling_decisions(
    manifest: ArtifactManifest,
    payload: bytes,
    model: type[ControllerSettlingDecision] | type[VisualSettlingDecision],
) -> dict[str, ControllerSettlingDecision | VisualSettlingDecision]:
    result: dict[str, ControllerSettlingDecision | VisualSettlingDecision] = {}
    for item in _jsonl(payload):
        if item.get("run_id") != manifest.run_id:
            raise ValueError("settling record run_id mismatch")
        step_id = str(item.get("step_id", ""))
        if not step_id or step_id in result:
            raise ValueError("settling artifact has missing or duplicate step_id")
        try:
            result[step_id] = model.model_validate(item["decision"])
        except (KeyError, ValueError) as exc:
            raise ValueError("settling artifact contains invalid typed data") from exc
    return result


def _jsonl(payload: bytes) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in payload.splitlines() if line]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("artifact is not valid JSONL") from exc


def _values(samples: Sequence[IdentificationSample], name: str) -> list[float]:
    return [
        float(sample.blendshapes[name])
        for sample in samples
        if name in sample.blendshapes
    ]


def _hysteresis_deltas(
    samples: Sequence[IdentificationSample], actuator: str, blendshape: str
) -> list[float]:
    values: list[float] = []
    for session in sorted({s.session_id for s in samples}):
        home_steps: dict[int, list[float]] = defaultdict(list)
        for sample in samples:
            if (
                sample.session_id == session
                and sample.actuator_name == actuator
                and sample.phase == "home"
                and blendshape in sample.blendshapes
            ):
                home_steps[sample.sequence_index].append(
                    float(sample.blendshapes[blendshape])
                )
        ordered = [float(np.mean(home_steps[index])) for index in sorted(home_steps)]
        if len(ordered) >= 3:
            pairs = [
                abs(ordered[index + 1] - ordered[index])
                for index in range(1, len(ordered) - 1, 3)
            ]
            if pairs:
                values.append(float(np.mean(pairs)))
    return values


def _return_home_deltas(
    samples: Sequence[IdentificationSample], actuator: str, blendshape: str
) -> list[float]:
    values: list[float] = []
    for session in sorted({sample.session_id for sample in samples}):
        home_steps: dict[int, list[float]] = defaultdict(list)
        for sample in samples:
            if (
                sample.session_id == session
                and sample.actuator_name == actuator
                and sample.phase == "home"
                and blendshape in sample.blendshapes
            ):
                home_steps[sample.sequence_index].append(
                    float(sample.blendshapes[blendshape])
                )
        ordered = [float(np.mean(home_steps[index])) for index in sorted(home_steps)]
        if len(ordered) >= 2:
            values.append(abs(ordered[-1] - ordered[0]))
    return values


def _effect_and_noise(
    samples: Sequence[IdentificationSample], actuator: str, blendshape: str
) -> tuple[float | None, float | None]:
    groups: dict[tuple[str, float], list[float]] = defaultdict(list)
    for sample in samples:
        if (
            sample.actuator_name == actuator
            and sample.phase != "home"
            and blendshape in sample.blendshapes
        ):
            groups[(sample.session_id, sample.normalized_position)].append(
                float(sample.blendshapes[blendshape])
            )
    session_effects: list[float] = []
    for session in sorted({key[0] for key in groups}):
        positive_means = [
            float(np.mean(values))
            for (group_session, position), values in groups.items()
            if group_session == session and position > 0
        ]
        negative_means = [
            float(np.mean(values))
            for (group_session, position), values in groups.items()
            if group_session == session and position < 0
        ]
        if positive_means and negative_means:
            session_effects.append(
                abs(float(np.mean(positive_means)) - float(np.mean(negative_means)))
                / 2.0
            )
    if not session_effects:
        return None, None
    signal = float(np.mean(session_effects))
    residual_sum_squares = 0.0
    residual_population_count = 0
    for values in groups.values():
        if len(values) < 2:
            continue
        group_mean = float(np.mean(values))
        residual_sum_squares += sum((value - group_mean) ** 2 for value in values)
        residual_population_count += len(values)
    if residual_population_count == 0:
        noise = None
    elif residual_sum_squares <= 1e-24:
        noise = 0.0
    else:
        noise = math.sqrt(residual_sum_squares / residual_population_count)
    return signal, noise


def _duration_metric(
    samples: Sequence[IdentificationSample],
    kind: Literal["controller", "first_visual", "visual_window", "total_visual"],
) -> MetricValue:
    durations: list[float] = []
    for sample in samples:
        if kind == "controller":
            start = sample.controller_command_ns
            end = sample.controller_settled_ns
        elif kind == "first_visual":
            start = sample.controller_command_ns
            end = sample.first_visual_sample_ns
        elif kind == "visual_window":
            start = sample.first_visual_sample_ns
            end = sample.visual_settled_ns
        else:
            start = sample.controller_command_ns
            end = sample.visual_settled_ns
        if start is not None and end is not None and end >= start:
            durations.append((end - start) / 1_000_000)
    return (
        _estimated_metric(
            float(np.mean(durations)),
            f"{kind} settling confidence interval is not estimated",
        )
        if durations
        else _missing_metric(f"{kind} settling timestamps are unavailable")
    )


def _variance_cell(row: str, column: str, values: Sequence[float]) -> MatrixCell:
    if len(values) < 2:
        return _missing_cell(
            row, column, "at least two samples are required for variance"
        )
    return _estimated_cell(
        row,
        column,
        float(np.var(values, ddof=0)),
        uncertainty_reason="variance confidence interval is not estimated",
    )


def _variance_components(
    samples: Sequence[IdentificationSample],
    blendshape: str,
    selected: Callable[[IdentificationSample], bool],
) -> tuple[MetricValue, MetricValue]:
    step_groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    session_groups: dict[str, list[float]] = defaultdict(list)
    for sample in samples:
        if selected(sample) and blendshape in sample.blendshapes:
            value = float(sample.blendshapes[blendshape])
            step_groups[(sample.session_id, sample.step_id)].append(value)
            session_groups[sample.session_id].append(value)
    within = [
        float(np.var(values, ddof=0))
        for values in step_groups.values()
        if len(values) >= 2
    ]
    if within:
        within_metric = _estimated_metric(
            float(np.mean(within)),
            "within-session variance confidence interval is not estimated",
        )
    else:
        within_metric = _missing_metric(
            "within-session variance requires repeated samples per step"
        )
    session_means = [float(np.mean(values)) for values in session_groups.values()]
    if len(session_means) >= 2:
        between_metric = _estimated_metric(
            float(np.var(session_means, ddof=0)),
            "between-session variance confidence interval is not estimated",
        )
    else:
        between_metric = _missing_metric(
            "between-session variance requires at least two sessions"
        )
    return within_metric, between_metric


def _metric_cell(row: str, column: str, metric: MetricValue) -> MatrixCell:
    return MatrixCell(row=row, column=column, **metric.model_dump())


def _mean_cell(
    row: str, column: str, values: Sequence[float], reason: str
) -> MatrixCell:
    return (
        _estimated_cell(
            row,
            column,
            float(np.mean(values)),
            uncertainty_reason="confidence interval is not estimated",
        )
        if values
        else _missing_cell(row, column, reason)
    )


def _estimated_cell(
    row: str,
    column: str,
    value: float,
    *,
    confidence: tuple[float, float] | None = None,
    uncertainty_reason: str | None = None,
) -> MatrixCell:
    return MatrixCell(
        row=row,
        column=column,
        value=value,
        status="estimated",
        reason=None,
        confidence_interval=None
        if confidence is None
        else ConfidenceInterval(lower=confidence[0], upper=confidence[1]),
        uncertainty_status="estimated" if confidence is not None else "unavailable",
        uncertainty_reason=uncertainty_reason,
    )


def _missing_cell(row: str, column: str, reason: str) -> MatrixCell:
    return MatrixCell(
        row=row,
        column=column,
        value=None,
        status="missing",
        reason=reason,
        confidence_interval=None,
        uncertainty_status="unavailable",
        uncertainty_reason=reason,
    )


def _undefined_cell(row: str, column: str, reason: str) -> MatrixCell:
    return MatrixCell(
        row=row,
        column=column,
        value=None,
        status="undefined",
        reason=reason,
        confidence_interval=None,
        uncertainty_status="unavailable",
        uncertainty_reason=reason,
    )


def _estimated_metric(value: float, uncertainty_reason: str) -> MetricValue:
    return MetricValue(
        value=value,
        status="estimated",
        reason=None,
        confidence_interval=None,
        uncertainty_status="unavailable",
        uncertainty_reason=uncertainty_reason,
    )


def _missing_metric(reason: str) -> MetricValue:
    return MetricValue(
        value=None,
        status="missing",
        reason=reason,
        confidence_interval=None,
        uncertainty_status="unavailable",
        uncertainty_reason=reason,
    )


def _undefined_metric(reason: str) -> MetricValue:
    return MetricValue(
        value=None,
        status="undefined",
        reason=reason,
        confidence_interval=None,
        uncertainty_status="unavailable",
        uncertainty_reason=reason,
    )


def _find_cell(matrix: NamedMatrix, row: str, column: str) -> MatrixCell:
    found = _find_cell_optional(matrix, row, column)
    if found is None:
        raise KeyError((row, column))
    return found


def _find_cell_optional(
    matrix: NamedMatrix, row: str, column: str
) -> MatrixCell | None:
    return next(
        (cell for cell in matrix.cells if cell.row == row and cell.column == column),
        None,
    )


def _required_value(metric: MetricValue) -> float:
    if metric.value is None:
        raise ValueError("numeric metric value is required")
    return metric.value


def _input(path: str, payload: bytes) -> AnalysisInput:
    return AnalysisInput(
        path=path, sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload)
    )


def _record(path: str, payload: bytes) -> ArtifactRecord:
    return ArtifactRecord(
        path=path, sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload)
    )


def _pretty(value: object) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"


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
