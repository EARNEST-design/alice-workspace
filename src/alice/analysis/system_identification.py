"""Deterministic, provenance-checked actuator system-identification analysis."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from alice.contracts.blendshapes import BlendshapeObservation, NonEmptyString, Sha256Hex
from alice.experiments.artifact_store import publish_generation
from alice.experiments.manifest import (
    ArtifactManifest,
    ArtifactRecord,
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
ANALYZER_REVISION: Final[Literal["system-identification/v1"]] = (
    "system-identification/v1"
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
    schema_version: Literal["identification-metrics/v1"]
    bootstrap_seed: Literal[20260831]
    bootstrap_replicates: int
    session_ids: tuple[NonEmptyString, ...]
    jacobian: NamedMatrix
    baseline_variance: NamedMatrix
    position_variance: NamedMatrix
    signal_to_noise: NamedMatrix
    hysteresis: NamedMatrix
    cross_effects: NamedMatrix
    actuator_coupling: NamedMatrix
    controller_settling_ms: MetricValue
    visual_settling_ms: MetricValue
    rank: int | None = Field(default=None, ge=0)
    singular_values: tuple[Annotated[float, Field(ge=0, allow_inf_nan=False)], ...]
    condition_number: MetricValue
    warnings: tuple[str, ...]


class RepeatabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["identification-repeatability/v1"]
    reference_seed: int
    repeat_seed: int
    absolute_delta: NamedMatrix
    outcome: Literal["pass", "fail", "inconclusive"]
    warnings: tuple[str, ...]


class AnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: NonEmptyString
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)


class AnalyzerIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    package_version: NonEmptyString
    git_revision: NonEmptyString | None
    analyzer_revision: Literal["system-identification/v1"]


class IdentificationConclusion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: Literal["pass", "fail", "inconclusive"]
    summary: NonEmptyString
    warnings: tuple[str, ...]


class IdentificationAnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    analyzer_revision: Literal["system-identification/v1"]
    bootstrap_seed: Literal[20260831]
    bootstrap_replicates: int = Field(gt=0)
    input_config_sha256: tuple[Sha256Hex, ...]


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
    for blendshape in blendshapes:
        for column in position_columns:
            actuator, encoded = column.split(":", 1)
            values = [
                s.blendshapes[blendshape]
                for s in samples
                if s.actuator_name == actuator
                and s.normalized_position == float(encoded)
                and blendshape in s.blendshapes
            ]
            position_cells.append(_variance_cell(blendshape, column, values))
    position_variance = NamedMatrix(
        row_labels=blendshapes,
        column_labels=position_columns,
        cells=tuple(position_cells),
    )

    baseline_cells = []
    for blendshape in blendshapes:
        for actuator in actuators:
            values = [
                s.blendshapes[blendshape]
                for s in samples
                if s.actuator_name == actuator
                and s.phase == "home"
                and blendshape in s.blendshapes
            ]
            baseline_cells.append(_variance_cell(blendshape, actuator, values))
    baseline_variance = NamedMatrix(
        row_labels=blendshapes, column_labels=actuators, cells=tuple(baseline_cells)
    )

    snr_cells: list[MatrixCell] = []
    hysteresis_cells: list[MatrixCell] = []
    for blendshape in blendshapes:
        for actuator in actuators:
            signal, noise = _effect_and_noise(samples, actuator, blendshape)
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
    snr = NamedMatrix(
        row_labels=blendshapes, column_labels=actuators, cells=tuple(snr_cells)
    )
    hysteresis = NamedMatrix(
        row_labels=blendshapes, column_labels=actuators, cells=tuple(hysteresis_cells)
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

    return IdentificationMetrics(
        schema_version="identification-metrics/v1",
        bootstrap_seed=BOOTSTRAP_SEED,
        bootstrap_replicates=BOOTSTRAP_REPLICATES,
        session_ids=sessions,
        jacobian=jacobian,
        baseline_variance=baseline_variance,
        position_variance=position_variance,
        signal_to_noise=snr,
        hysteresis=hysteresis,
        cross_effects=jacobian,
        actuator_coupling=coupling,
        controller_settling_ms=_duration_metric(samples, "controller"),
        visual_settling_ms=_duration_metric(samples, "visual"),
        rank=rank,
        singular_values=tuple(float(value) for value in singular),
        condition_number=condition,
        warnings=tuple(warnings),
    )


def compare_repeat_run(
    reference: IdentificationMetrics, repeat: IdentificationMetrics
) -> RepeatabilityResult:
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
    return RepeatabilityResult(
        schema_version="identification-repeatability/v1",
        reference_seed=reference.bootstrap_seed,
        repeat_seed=repeat.bootstrap_seed,
        absolute_delta=NamedMatrix(
            row_labels=rows, column_labels=columns, cells=tuple(cells)
        ),
        outcome="inconclusive" if incomplete else "pass",
        warnings=tuple(warnings),
    )


def analyze_identification_artifacts(run_dirs: Sequence[Path]) -> IdentificationMetrics:
    samples: list[IdentificationSample] = []
    seen: set[str] = set()
    for run_dir in run_dirs:
        manifest, artifacts = _verified_run(run_dir)
        if manifest.run_id in seen:
            raise ValueError(f"duplicate run_id: {manifest.run_id}")
        seen.add(manifest.run_id)
        samples.extend(_samples_from_artifacts(manifest, artifacts))
    return estimate_local_jacobian(samples)


def publish_identification_analysis(
    run_dirs: Sequence[Path], output_dir: Path, *, generation_id: str | None = None
) -> tuple[IdentificationAnalysisManifest, Path]:
    verified = [(Path(path).resolve(), *_verified_run(path)) for path in run_dirs]
    run_ids = [manifest.run_id for _, manifest, _ in verified]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("duplicate run_id in analysis inputs")
    metrics = estimate_local_jacobian(
        [
            sample
            for _, manifest, artifacts in verified
            for sample in _samples_from_artifacts(manifest, artifacts)
        ]
    )
    conclusion = IdentificationConclusion(
        outcome="inconclusive",
        summary=(
            "System-identification metrics were computed; acceptance thresholds "
            "and a held-out repeat gate remain required."
        ),
        warnings=metrics.warnings,
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
    )
    payloads = {
        "identification-metrics.json": _pretty(metrics.model_dump(mode="json")),
        "conclusion.json": _pretty(conclusion.model_dump(mode="json")),
        "actuator-identification-conclusion.md": report.encode(),
    }
    inputs: dict[str, AnalysisInput] = {}
    config_hashes: list[str] = []
    for run_path, manifest, artifacts in verified:
        metadata = manifest.identification_metadata
        if metadata is None:  # enforced by ArtifactManifest, retained for typing
            raise ValueError("identification metadata is required")
        config_hashes.append(metadata.config_sha256)
        raw_manifest = (run_path / "manifest.json").read_bytes()
        inputs[f"{manifest.run_id}.manifest"] = _input("manifest.json", raw_manifest)
        for name, payload in artifacts.items():
            inputs[f"{manifest.run_id}.{name}"] = _input(name, payload)
    analysis_config = IdentificationAnalysisConfig(
        analyzer_revision=ANALYZER_REVISION,
        bootstrap_seed=BOOTSTRAP_SEED,
        bootstrap_replicates=BOOTSTRAP_REPLICATES,
        input_config_sha256=tuple(sorted(config_hashes)),
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


def _verified_run(run_dir: Path) -> tuple[ArtifactManifest, dict[str, bytes]]:
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
    config_payload = json.dumps(
        manifest.config, sort_keys=True, separators=(",", ":")
    ).encode()
    if hashlib.sha256(config_payload).hexdigest() != metadata.config_sha256:
        raise ValueError("config checksum does not match identification provenance")
    required = (
        "commands.jsonl",
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
    return manifest, artifacts


def _samples_from_artifacts(
    manifest: ArtifactManifest, artifacts: Mapping[str, bytes]
) -> list[IdentificationSample]:
    commands = _normal_commands(manifest, artifacts["commands.jsonl"])
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
        if observation.run_id != manifest.run_id or observation.validity != "valid":
            raise ValueError(
                "identification observation is invalid or has wrong run identity"
            )
        scores = {score.name: score.score for score in observation.scores}
        controller_decision = controller[step_id]
        visual_decision = visual[step_id]
        if not isinstance(
            controller_decision, ControllerSettlingDecision
        ) or not isinstance(visual_decision, VisualSettlingDecision):
            raise ValueError("settling artifact decision types are inconsistent")
        result.append(
            IdentificationSample(
                session_id=manifest.run_id,
                step_id=step_id,
                sequence_index=order[step_id],
                actuator_name=command["actuator_name"],
                normalized_position=command["normalized_position"],
                phase=command["phase"],
                blendshapes=scores,
                controller_command_ns=int(command["monotonic_ns"]),
                controller_settled_ns=max(
                    sample.observed_monotonic_ns
                    for sample in controller_decision.samples
                ),
                first_visual_sample_ns=observation.monotonic_ns,
                visual_settled_ns=visual_decision.decided_monotonic_ns,
            )
        )
    return result


def _normal_commands(
    manifest: ArtifactManifest, payload: bytes
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
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
        except (KeyError, ValueError) as exc:
            raise ValueError("command artifact contains invalid typed data") from exc
        if step.step_id in result:
            raise ValueError("command artifact contains duplicate normal step_id")
        result[step.step_id] = item
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
        if len(ordered) >= 2:
            values.append(ordered[-1] - ordered[0])
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
    positive = [
        value
        for (session, position), values in groups.items()
        if position > 0
        for value in values
    ]
    negative = [
        value
        for (session, position), values in groups.items()
        if position < 0
        for value in values
    ]
    if not positive or not negative:
        return None, None
    signal = abs(float(np.mean(positive)) - float(np.mean(negative))) / 2.0
    residuals = [
        value - float(np.mean(values)) for values in groups.values() for value in values
    ]
    noise = float(np.std(residuals, ddof=1)) if len(residuals) > 1 else None
    return signal, noise


def _duration_metric(
    samples: Sequence[IdentificationSample], kind: Literal["controller", "visual"]
) -> MetricValue:
    durations: list[float] = []
    for sample in samples:
        start = sample.controller_command_ns
        end = (
            sample.controller_settled_ns
            if kind == "controller"
            else sample.visual_settled_ns
        )
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
        float(np.var(values, ddof=1)),
        uncertainty_reason="variance confidence interval is not estimated",
    )


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
