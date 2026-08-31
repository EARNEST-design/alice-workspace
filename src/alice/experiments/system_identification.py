"""Deterministic, mock-only guarded actuator system-identification runs."""

from __future__ import annotations

import hashlib
import json
import platform
import statistics
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.actuation import (
    ActuatorStatus,
    ActuatorTarget,
    ControllerOutputSample,
    PoseRequest,
)
from alice.contracts.blendshapes import (
    BlendshapeObservation,
    NonEmptyString,
    ObservationValidity,
    Sha256Hex,
)
from alice.experiments.artifact_store import publish_generation, sha256_path
from alice.experiments.manifest import (
    ArtifactManifest,
    ArtifactRecord,
    FailureCategory,
    FailureRecord,
    IdentificationObserverProvenance,
    IdentificationRunMetadata,
    NegotiatedCameraSettings,
    RunKind,
    RunStatus,
)
from alice.hardware.adapter import ActuatorAdapter
from alice.hardware.manifest import load_manifest
from alice.hardware.mock_adapter import MockActuatorAdapter, MockAdapterScript
from alice.safety.supervisor import (
    AbortReason,
    AuthorizationDecision,
    RecoveryAuthorization,
    RecoveryWaitReason,
    RunState,
    SafetyFault,
    SafetySupervisor,
    TransitionResult,
)


class IdentificationObserver(Protocol):
    """Hardware-independent observation source correlated by run and step."""

    @property
    def provenance(self) -> IdentificationObserverProvenance: ...

    def observe(self, *, run_id: str, step_id: str) -> BlendshapeObservation: ...


class IdentificationStep(BaseModel):
    """One normal (non-recovery) command and measurement step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: NonEmptyString
    actuator_name: NonEmptyString
    normalized_position: Annotated[
        float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    ]
    phase: Literal["home", "positive", "negative"]

    @model_validator(mode="after")
    def validate_phase_position(self) -> IdentificationStep:
        if self.phase == "home" and self.normalized_position != 0.0:
            raise ValueError("home step must target zero")
        if self.phase == "positive" and self.normalized_position <= 0.0:
            raise ValueError("positive step must target a positive offset")
        if self.phase == "negative" and self.normalized_position >= 0.0:
            raise ValueError("negative step must target a negative offset")
        return self


class NamedMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonEmptyString
    value: Annotated[float, Field(allow_inf_nan=False)]


class VisualHomeBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    means: tuple[NamedMetric, ...]


class ControllerSettlingDecision(BaseModel):
    """Typed controller-output evidence, explicitly not mechanical truth."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_reached: bool
    requested_targets: tuple[ActuatorTarget, ...]
    samples: tuple[ControllerOutputSample, ...]
    decided_monotonic_ns: int = Field(ge=0)


class VisualSettlingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    means: tuple[NamedMetric, ...]
    variances: tuple[NamedMetric, ...]
    baseline_deltas: tuple[NamedMetric, ...]
    variance_accepted: bool
    baseline_accepted: bool | None
    established_home_baseline: bool
    home_verified: bool | None
    decided_monotonic_ns: int = Field(ge=0)


class IdentificationConfig(BaseModel):
    """Reviewed mock-run configuration; hardware selection is unrepresentable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["identification-config/v1"]
    run_id: NonEmptyString
    adapter: Literal["mock"]
    hardware_id: NonEmptyString
    calibration_sha256: Sha256Hex
    hardware_manifest_path: NonEmptyString
    hardware_manifest_sha256: Sha256Hex
    hardware_manifest_canonical_sha256: Sha256Hex
    actuator_names: tuple[NonEmptyString, ...]
    offsets: tuple[Annotated[float, Field(allow_inf_nan=False)], ...]
    samples_per_step: int = Field(ge=2)
    command_interval_ms: int = Field(gt=0)
    controller_settle_ms: int = Field(ge=0)
    visual_settle_ms: int = Field(ge=0)
    sample_interval_ms: int = Field(ge=0)
    step_timeout_ms: int = Field(gt=0)
    command_ttl_ms: int = Field(gt=0)
    maximum_visual_variance: Annotated[
        float, Field(ge=0.0, allow_inf_nan=False)
    ]
    home_delta_tolerances: Mapping[
        NonEmptyString, Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    ]
    recovery_timeout_ms: int = Field(gt=0)
    maximum_recovery_attempts: int = Field(gt=0)
    random_seeds: tuple[int, ...]
    provenance: Mapping[NonEmptyString, NonEmptyString]
    retention: Literal["derived_observations_only"]
    observer: IdentificationObserverProvenance
    mock_behavior: MockAdapterScript = Field(default_factory=MockAdapterScript)

    @model_validator(mode="after")
    def validate_experiment_shape(self) -> IdentificationConfig:
        if not self.actuator_names:
            raise ValueError("at least one actuator_name is required")
        if len(self.actuator_names) != len(set(self.actuator_names)):
            raise ValueError("actuator_names must be unique")
        if len(self.offsets) != 2:
            raise ValueError(
                "initial mock run requires one positive and one negative offset"
            )
        positive, negative = self.offsets
        if positive <= 0.0 or negative >= 0.0 or positive != -negative:
            raise ValueError("offsets must be equal-magnitude positive then negative")
        if abs(positive) > 0.1:
            raise ValueError("initial mock offset magnitude cannot exceed 0.1")
        if not self.provenance:
            raise ValueError("provenance must not be empty")
        if not self.home_delta_tolerances:
            raise ValueError("home_delta_tolerances must not be empty")
        return self


class _RunLog:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.records: dict[str, list[dict[str, Any]]] = {
            "commands.jsonl": [],
            "statuses.jsonl": [],
            "observations.jsonl": [],
            "transitions.jsonl": [],
            "faults.jsonl": [],
            "controller-settling.jsonl": [],
            "visual-settling.jsonl": [],
        }

    def append(self, artifact: str, record: Mapping[str, Any]) -> None:
        materialized = dict(record)
        if materialized.get("run_id") != self.run_id:
            raise ValueError("artifact record run_id mismatch")
        if not materialized.get("step_id"):
            raise ValueError("artifact record requires step_id")
        self.records[artifact].append(materialized)

    def transition(
        self,
        *,
        step_id: str,
        operation: str,
        result: TransitionResult | AuthorizationDecision,
        monotonic_ns: int,
    ) -> None:
        self.append(
            "transitions.jsonl",
            {
                "run_id": self.run_id,
                "step_id": step_id,
                "operation": operation,
                "monotonic_ns": monotonic_ns,
                "accepted": (
                    result.authorized
                    if isinstance(result, AuthorizationDecision)
                    else result.accepted
                ),
                "state": result.state.value,
            },
        )
        if result.fault is not None:
            self.fault(step_id=step_id, fault=result.fault)

    def fault(self, *, step_id: str, fault: SafetyFault) -> None:
        self.append(
            "faults.jsonl",
            {
                "run_id": self.run_id,
                "step_id": step_id,
                "fault": fault.model_dump(mode="json"),
            },
        )


class _ControlledAbort(Exception):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        category: FailureCategory,
        camera_loss: bool = False,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.category = category
        self.camera_loss = camera_loss


def run_mock_identification(
    config: IdentificationConfig,
    observer: IdentificationObserver,
    supervisor: SafetySupervisor,
    output_dir: Path,
    clock: Callable[[], int],
    sleeper: Callable[[float], object],
) -> ArtifactManifest:
    """Run mock-only identification with the exact trusted mock adapter."""

    adapter = MockActuatorAdapter(
        manifest=supervisor.manifest,
        clock=clock,
        permit_verifier=supervisor.actuation_permit_verifier,
        script=config.mock_behavior,
    )
    return _run_identification_core(
        config=config,
        observer=observer,
        supervisor=supervisor,
        adapter=adapter,
        output_dir=output_dir,
        clock=clock,
        sleeper=sleeper,
    )


def _run_identification_core(
    *,
    config: IdentificationConfig,
    observer: IdentificationObserver,
    supervisor: SafetySupervisor,
    adapter: ActuatorAdapter,
    output_dir: Path,
    clock: Callable[[], int],
    sleeper: Callable[[float], object],
) -> ArtifactManifest:
    """Run the deterministic sequence for a trusted composition root.

    The monotonic ``clock`` is the root of request validity and recovery timing.
    If it itself fails, safe reconciliation timestamps cannot be constructed, so
    that exception propagates without a false terminal-state or artifact claim.
    This is acceptable only for this mock entrypoint. A future hardware composition
    must add an independent watchdog and revocation path outside this process.
    """

    _validate_new_output(output_dir)
    started_at = datetime.now(UTC)
    log = _RunLog(config.run_id)
    status = RunStatus.COMPLETED
    failure: FailureRecord | None = None
    aborted_reason: str | None = None
    current_step_id = "run-start"
    baseline: VisualHomeBaseline | None = None
    runtime_observer: IdentificationObserverProvenance | None
    composition_problem: tuple[str, str, FailureCategory] | None
    try:
        runtime_observer = observer.provenance
    except Exception:
        runtime_observer = None
        composition_problem = (
            "observer-provenance-error",
            "runtime observer provenance could not be attested",
            FailureCategory.CAMERA_LOSS,
        )
    else:
        composition_problem = _composition_problem(
            config=config,
            supervisor=supervisor,
            observer=runtime_observer,
        )
    runtime_identity = adapter.identity
    if composition_problem is not None:
        status = RunStatus.ABORTED
        code, detail, composition_category = composition_problem
        failure = FailureRecord(
            category=composition_category,
            error_type=_safe_error_type(code),
        )
        aborted_reason = f"identification aborted: {code}"
        log.fault(
            step_id=current_step_id,
            fault=SafetyFault(
                code=code,
                detail=detail,
                occurred_monotonic_ns=clock(),
            ),
        )
        if supervisor.state is RunState.ARMED:
            cancellation = supervisor.cancel_armed(detail)
            log.transition(
                step_id=current_step_id,
                operation="cancel-armed",
                result=cancellation,
                monotonic_ns=clock(),
            )
    else:
        start_result = supervisor.start()
        log.transition(
            step_id=current_step_id,
            operation="start",
            result=start_result,
            monotonic_ns=clock(),
        )
        if not start_result.accepted or start_result.state is not RunState.RUNNING:
            status = RunStatus.ABORTED
            failure = FailureRecord(
                category=FailureCategory.SAFETY_ERROR,
                error_type="SafetyStartRejected",
            )
            aborted_reason = "identification aborted: supervisor start rejected"
            if start_result.state is RunState.ABORTING:
                recovery_exhausted = _abort_and_recover(
                    error=_ControlledAbort(
                        "safety-start-rejected",
                        "supervisor entered aborting during start",
                        category=FailureCategory.SAFETY_ERROR,
                    ),
                    step_id=current_step_id,
                    config=config,
                    observer=observer,
                    baseline=None,
                    supervisor=supervisor,
                    adapter=adapter,
                    clock=clock,
                    sleeper=sleeper,
                    log=log,
                )
                if recovery_exhausted:
                    failure = FailureRecord(
                        category=FailureCategory.RECOVERY_ERROR,
                        error_type="RecoveryExhausted",
                    )
        else:
            try:
                final_home_verified = False
                for step in _steps(config):
                    current_step_id = step.step_id
                    baseline, final_home_verified = _execute_step(
                        config=config,
                        step=step,
                        baseline=baseline,
                        observer=observer,
                        supervisor=supervisor,
                        adapter=adapter,
                        clock=clock,
                        sleeper=sleeper,
                        log=log,
                    )
                if not final_home_verified or baseline is None:
                    raise _ControlledAbort(
                        "final-home-not-verified",
                        "final independent visual Home evidence is absent",
                        category=FailureCategory.VISUAL_ERROR,
                    )
                completion = supervisor.complete_run()
                log.transition(
                    step_id=current_step_id,
                    operation="complete-run",
                    result=completion,
                    monotonic_ns=clock(),
                )
                if not completion.accepted or completion.state is RunState.RUNNING:
                    raise _ControlledAbort(
                        "safety-completion-rejected",
                        "supervisor did not leave RUNNING after final Home",
                        category=FailureCategory.SAFETY_ERROR,
                    )
            except _ControlledAbort as error:
                status = RunStatus.ABORTED
                failure = FailureRecord(
                    category=error.category,
                    error_type=_safe_error_type(error.code),
                )
                aborted_reason = f"identification aborted: {error.code}"
                recovery_exhausted = _abort_and_recover(
                    error=error,
                    step_id=current_step_id,
                    config=config,
                    observer=observer,
                    baseline=baseline,
                    supervisor=supervisor,
                    adapter=adapter,
                    clock=clock,
                    sleeper=sleeper,
                    log=log,
                )
                if recovery_exhausted:
                    failure = FailureRecord(
                        category=FailureCategory.RECOVERY_ERROR,
                        error_type="RecoveryExhausted",
                    )

    files = _artifact_payloads(config=config, log=log, status=status)
    artifacts = {
        name: _record_for_bytes(name, payload) for name, payload in files.items()
    }
    manifest = ArtifactManifest(
        schema_version="artifact-manifest/v1",
        run_kind=RunKind.ACTUATOR_IDENTIFICATION,
        run_id=config.run_id,
        status=status,
        started_at=started_at,
        ended_at=datetime.now(UTC),
        observation_count=len(log.records["observations.jsonl"]),
        config=config.model_dump(mode="json"),
        artifacts=artifacts,
        git_revision=_git_revision(),
        dependency_lock_path="uv.lock" if Path("uv.lock").is_file() else None,
        dependency_lock_sha256=(
            sha256_path(Path("uv.lock")) if Path("uv.lock").is_file() else None
        ),
        python_version=platform.python_version(),
        platform_system=platform.system() or "unknown",
        platform_release=platform.release() or "unknown",
        platform_machine=platform.machine() or "unknown",
        camera_settings=(
            runtime_observer.camera_settings
            if runtime_observer is not None
            else NegotiatedCameraSettings.unavailable()
        ),
        aborted_reason=aborted_reason,
        failure=failure,
        conclusion=None,
        identification_metadata=IdentificationRunMetadata(
            adapter_identity=runtime_identity,
            observer=runtime_observer,
            expected_observer=config.observer,
            safety_limits=supervisor.limits,
            preflight=supervisor.preflight_evidence,
            approval=supervisor.operator_approval,
            hardware_manifest_path=config.hardware_manifest_path,
            hardware_manifest_sha256=config.hardware_manifest_sha256,
            hardware_manifest_canonical_sha256=(
                config.hardware_manifest_canonical_sha256
            ),
            calibration_sha256=config.calibration_sha256,
            config_sha256=_config_sha256(config),
        ),
    )
    files["manifest.json"] = _json_bytes(manifest.model_dump(mode="json"), indent=2)
    publish_generation(output_dir.parent.resolve(), output_dir.name, files)
    return manifest


def _composition_problem(
    *,
    config: IdentificationConfig,
    supervisor: SafetySupervisor,
    observer: IdentificationObserverProvenance,
) -> tuple[str, str, FailureCategory] | None:
    if observer != config.observer:
        return (
            "observer-identity-mismatch",
            "runtime observer provenance differs from reviewed config",
            FailureCategory.CAMERA_LOSS,
        )
    if (
        supervisor.manifest.hardware_id != config.hardware_id
        or supervisor.manifest.calibration_sha256 != config.calibration_sha256
    ):
        return (
            "manifest-identity-mismatch",
            "supervisor manifest differs from config",
            FailureCategory.SAFETY_ERROR,
        )
    manifest_path = Path(config.hardware_manifest_path)
    if not manifest_path.is_file():
        return (
            "manifest-file-missing",
            "configured hardware manifest does not exist",
            FailureCategory.SAFETY_ERROR,
        )
    try:
        manifest_sha256 = sha256_path(manifest_path)
        configured_manifest = load_manifest(manifest_path)
    except (OSError, ValueError):
        return (
            "manifest-file-invalid",
            "configured hardware manifest could not be read and validated",
            FailureCategory.SAFETY_ERROR,
        )
    if manifest_sha256 != config.hardware_manifest_sha256:
        return (
            "manifest-file-hash-mismatch",
            "hardware manifest checksum differs",
            FailureCategory.SAFETY_ERROR,
        )
    if (
        configured_manifest.canonical_sha256
        != config.hardware_manifest_canonical_sha256
    ):
        return (
            "manifest-canonical-hash-mismatch",
            "configured canonical manifest checksum differs",
            FailureCategory.SAFETY_ERROR,
        )
    if supervisor.manifest.canonical_sha256 != configured_manifest.canonical_sha256:
        return (
            "supervisor-manifest-mismatch",
            "supervisor is not bound to the complete configured manifest",
            FailureCategory.SAFETY_ERROR,
        )
    return None


def _config_sha256(config: IdentificationConfig) -> str:
    payload = json.dumps(
        config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _steps(config: IdentificationConfig) -> tuple[IdentificationStep, ...]:
    steps: list[IdentificationStep] = []
    index = 0
    for actuator_name in config.actuator_names:
        sequence: tuple[
            tuple[float, Literal["home", "positive", "negative"]], ...
        ] = (
            (0.0, "home"),
            (config.offsets[0], "positive"),
            (0.0, "home"),
            (config.offsets[1], "negative"),
            (0.0, "home"),
        )
        for position, phase in sequence:
            index += 1
            steps.append(
                IdentificationStep(
                    step_id=f"step-{index:04d}",
                    actuator_name=actuator_name,
                    normalized_position=position,
                    phase=phase,
                )
            )
    return tuple(steps)


def _execute_step(
    *,
    config: IdentificationConfig,
    step: IdentificationStep,
    baseline: VisualHomeBaseline | None,
    observer: IdentificationObserver,
    supervisor: SafetySupervisor,
    adapter: ActuatorAdapter,
    clock: Callable[[], int],
    sleeper: Callable[[float], object],
    log: _RunLog,
) -> tuple[VisualHomeBaseline | None, bool]:
    step_started_ns = clock()
    _guarded_sleep(
        config.command_interval_ms / 1_000,
        step_id=step.step_id,
        operation="command-interval-watchdog",
        supervisor=supervisor,
        clock=clock,
        sleeper=sleeper,
        log=log,
    )
    _check_timeout(config, step_started_ns, clock())
    _guard_running(
        step_id=step.step_id,
        operation="pre-authorize-watchdog",
        supervisor=supervisor,
        clock=clock,
        log=log,
    )
    request = PoseRequest(
        schema_version="pose-request/v1",
        request_id=f"{config.run_id}-{step.step_id}",
        run_id=config.run_id,
        hardware_id=config.hardware_id,
        calibration_sha256=config.calibration_sha256,
        issued_monotonic_ns=clock(),
        expires_monotonic_ns=clock() + config.command_ttl_ms * 1_000_000,
        targets=(
            ActuatorTarget(
                actuator_name=step.actuator_name,
                normalized_position=step.normalized_position,
            ),
        ),
    )
    decision = supervisor.authorize(request)
    log.transition(
        step_id=step.step_id,
        operation="authorize",
        result=decision,
        monotonic_ns=clock(),
    )
    if not decision.authorized:
        raise _ControlledAbort(
            "authorization-rejected",
            "supervisor rejected step",
            category=FailureCategory.SAFETY_ERROR,
        )
    _log_command(log, step=step, authorization=decision, kind="normal", clock=clock)
    try:
        actuator_status = adapter.apply(decision)
    except Exception as exc:
        raise _ControlledAbort(
            "adapter-error",
            type(exc).__name__,
            category=FailureCategory.CONTROLLER_ERROR,
        ) from exc
    _log_status(log, step.step_id, actuator_status, clock())
    controller_decision = _controller_settling_decision(
        status=actuator_status,
        request=request,
        supervisor=supervisor,
        now_ns=clock(),
    )
    log.append(
        "controller-settling.jsonl",
        {
            "run_id": config.run_id,
            "step_id": step.step_id,
            "decision": controller_decision.model_dump(mode="json"),
        },
    )
    recorded = supervisor.record_status(actuator_status)
    log.transition(
        step_id=step.step_id,
        operation="record-status",
        result=recorded,
        monotonic_ns=clock(),
    )
    if not recorded.accepted or recorded.state is not RunState.RUNNING:
        raise _ControlledAbort(
            "controller-fault",
            "controller status was not accepted",
            category=FailureCategory.CONTROLLER_ERROR,
        )
    if not controller_decision.target_reached or not controller_decision.samples:
        raise _ControlledAbort(
            "controller-settling-unverified",
            "controller output lacks terminal target evidence",
            category=FailureCategory.CONTROLLER_ERROR,
        )

    _guarded_sleep(
        config.controller_settle_ms / 1_000,
        step_id=step.step_id,
        operation="controller-settle-watchdog",
        supervisor=supervisor,
        clock=clock,
        sleeper=sleeper,
        log=log,
    )
    _check_timeout(config, step_started_ns, clock())
    _guarded_sleep(
        config.visual_settle_ms / 1_000,
        step_id=step.step_id,
        operation="visual-settle-watchdog",
        supervisor=supervisor,
        clock=clock,
        sleeper=sleeper,
        log=log,
    )
    _check_timeout(config, step_started_ns, clock())
    observations: list[BlendshapeObservation] = []
    for sample_index in range(config.samples_per_step):
        if sample_index:
            _guarded_sleep(
                config.sample_interval_ms / 1_000,
                step_id=step.step_id,
                operation="sample-interval-watchdog",
                supervisor=supervisor,
                clock=clock,
                sleeper=sleeper,
                log=log,
            )
        _check_timeout(config, step_started_ns, clock())
        try:
            item = observer.observe(run_id=config.run_id, step_id=step.step_id)
        except Exception as exc:
            raise _ControlledAbort(
                "observer-error",
                type(exc).__name__,
                category=FailureCategory.CAMERA_LOSS,
                camera_loss=True,
            ) from exc
        _guard_running(
            step_id=step.step_id,
            operation="post-observer-watchdog",
            supervisor=supervisor,
            clock=clock,
            log=log,
        )
        _check_timeout(config, step_started_ns, clock())
        _validate_observation(
            item,
            config=config,
            status_ns=actuator_status.reported_monotonic_ns,
        )
        observations.append(item)
        log.append(
            "observations.jsonl",
            {
                "run_id": config.run_id,
                "step_id": step.step_id,
                "sample_index": sample_index,
                "observation": item.model_dump(mode="json"),
            },
        )
    _guard_running(
        step_id=step.step_id,
        operation="post-final-sample-watchdog",
        supervisor=supervisor,
        clock=clock,
        log=log,
    )
    _check_timeout(config, step_started_ns, clock())
    visual_decision, new_baseline = _visual_decision(
        config=config,
        step=step,
        observations=observations,
        baseline=baseline,
        now_ns=clock(),
    )
    log.append(
        "visual-settling.jsonl",
        {
            "run_id": config.run_id,
            "step_id": step.step_id,
            "decision": visual_decision.model_dump(mode="json"),
        },
    )
    if not visual_decision.variance_accepted:
        raise _ControlledAbort(
            "visual-variance-breach",
            "visual variance exceeds configured threshold",
            category=FailureCategory.VISUAL_ERROR,
        )
    if step.phase == "home" and visual_decision.home_verified is not True:
        raise _ControlledAbort(
            "visual-home-baseline-mismatch",
            "stable Home observation differs from the initial Home baseline",
            category=FailureCategory.VISUAL_ERROR,
        )
    if step.phase == "home":
        log.append(
            "transitions.jsonl",
            {
                "run_id": config.run_id,
                "step_id": step.step_id,
                "operation": "home-verified",
                "monotonic_ns": clock(),
                "accepted": visual_decision.home_verified is True,
                "state": supervisor.state.value,
            },
        )
    return new_baseline, visual_decision.home_verified is True


def _validate_observation(
    observation: BlendshapeObservation,
    *,
    config: IdentificationConfig,
    status_ns: int,
) -> None:
    if observation.run_id != config.run_id:
        raise _ControlledAbort(
            "observation-run-mismatch",
            "wrong observation run",
            category=FailureCategory.CAMERA_LOSS,
        )
    if (
        observation.camera_id != config.observer.camera_id
        or observation.detector != config.observer.detector
        or observation.detector_model_sha256
        != config.observer.detector_model_sha256
    ):
        raise _ControlledAbort(
            "observer-identity-mismatch",
            "observation provenance differs from reviewed config",
            category=FailureCategory.CAMERA_LOSS,
        )
    if observation.validity is not ObservationValidity.VALID:
        raise _ControlledAbort(
            "camera-loss",
            "observer did not return a face",
            category=FailureCategory.CAMERA_LOSS,
            camera_loss=True,
        )
    if observation.monotonic_ns < status_ns:
        raise _ControlledAbort(
            "stale-observation",
            "observation predates controller status",
            category=FailureCategory.CAMERA_LOSS,
        )


def _check_timeout(config: IdentificationConfig, start_ns: int, now_ns: int) -> None:
    if now_ns - start_ns >= config.step_timeout_ms * 1_000_000:
        raise _ControlledAbort(
            "step-timeout",
            "step exceeded its configured timeout",
            category=FailureCategory.TIMEOUT,
        )


def _guarded_sleep(
    seconds: float,
    *,
    step_id: str,
    operation: str,
    supervisor: SafetySupervisor,
    clock: Callable[[], int],
    sleeper: Callable[[float], object],
    log: _RunLog,
) -> None:
    try:
        sleeper(seconds)
    except Exception as exc:
        raise _ControlledAbort(
            "sleeper-error",
            type(exc).__name__,
            category=FailureCategory.TIMEOUT,
        ) from exc
    _guard_running(
        step_id=step_id,
        operation=operation,
        supervisor=supervisor,
        clock=clock,
        log=log,
    )


def _guard_running(
    *,
    step_id: str,
    operation: str,
    supervisor: SafetySupervisor,
    clock: Callable[[], int],
    log: _RunLog,
) -> None:
    result = supervisor.watchdog(clock())
    log.transition(
        step_id=step_id,
        operation=operation,
        result=result,
        monotonic_ns=clock(),
    )
    if not result.accepted:
        raise _ControlledAbort(
            result.fault.code if result.fault is not None else "watchdog-rejected",
            "runtime watchdog rejected progress",
            category=FailureCategory.TIMEOUT,
        )


def _visual_decision(
    *,
    config: IdentificationConfig,
    step: IdentificationStep,
    observations: list[BlendshapeObservation],
    baseline: VisualHomeBaseline | None,
    now_ns: int,
) -> tuple[VisualSettlingDecision, VisualHomeBaseline | None]:
    schemas = [tuple(score.name for score in item.scores) for item in observations]
    if len(set(schemas)) != 1:
        raise _ControlledAbort(
            "blendshape-schema-mismatch",
            "score schema changed",
            category=FailureCategory.VISUAL_ERROR,
        )
    values: dict[str, list[float]] = {name: [] for name in schemas[0]}
    for item in observations:
        for score in item.scores:
            values[score.name].append(score.score)
    means = tuple(
        NamedMetric(name=name, value=statistics.fmean(samples))
        for name, samples in sorted(values.items())
    )
    variances = tuple(
        NamedMetric(name=name, value=statistics.pvariance(samples))
        for name, samples in sorted(values.items())
    )
    variance_accepted = all(
        item.value <= config.maximum_visual_variance for item in variances
    )
    baseline_deltas: tuple[NamedMetric, ...] = ()
    baseline_accepted: bool | None = None
    established = False
    home_verified: bool | None = None
    next_baseline = baseline
    if step.phase == "home":
        if baseline is None:
            expected_names = set(config.home_delta_tolerances)
            observed_names = {item.name for item in means}
            baseline_accepted = expected_names == observed_names
            established = baseline_accepted and variance_accepted
            home_verified = established
            if established:
                next_baseline = VisualHomeBaseline(means=means)
        else:
            reference = {item.name: item.value for item in baseline.means}
            baseline_deltas = tuple(
                NamedMetric(
                    name=item.name,
                    value=abs(item.value - reference[item.name]),
                )
                for item in means
                if item.name in reference
            )
            exact_schema = {item.name for item in means} == set(reference) == set(
                config.home_delta_tolerances
            )
            baseline_accepted = exact_schema and all(
                item.value <= config.home_delta_tolerances[item.name]
                for item in baseline_deltas
            )
            home_verified = variance_accepted and baseline_accepted
    return (
        VisualSettlingDecision(
            means=means,
            variances=variances,
            baseline_deltas=baseline_deltas,
            variance_accepted=variance_accepted,
            baseline_accepted=baseline_accepted,
            established_home_baseline=established,
            home_verified=home_verified,
            decided_monotonic_ns=now_ns,
        ),
        next_baseline,
    )


def _abort_and_recover(
    *,
    error: _ControlledAbort,
    step_id: str,
    config: IdentificationConfig,
    observer: IdentificationObserver,
    baseline: VisualHomeBaseline | None,
    supervisor: SafetySupervisor,
    adapter: ActuatorAdapter,
    clock: Callable[[], int],
    sleeper: Callable[[float], object],
    log: _RunLog,
) -> bool:
    if supervisor.state is RunState.RUNNING:
        result = supervisor.abort(
            (
                AbortReason.CAMERA_LOSS
                if error.camera_loss
                else AbortReason.OPERATOR_REQUEST
            )
        )
        log.transition(
            step_id=step_id,
            operation="abort",
            result=result,
            monotonic_ns=clock(),
        )
    elif supervisor.state is RunState.ABORTING:
        # The failed authorize/status operation already initiated recovery.
        result = TransitionResult(
            accepted=False,
            state=supervisor.state,
            fault=supervisor.fault,
            recovery_request=supervisor.recovery_request,
            recovery_authorization=supervisor.recovery_authorization,
            recovery_wait=supervisor.recovery_wait,
        )
    else:
        return False

    recovery_index = 0
    recovery_operations = 0
    recovery_deadline_ns = clock() + config.recovery_timeout_ms * 1_000_000
    while supervisor.state is RunState.ABORTING:
        if (
            clock() >= recovery_deadline_ns
            or recovery_operations >= config.maximum_recovery_attempts
        ):
            _exhaust_recovery(
                step_id=step_id,
                supervisor=supervisor,
                clock=clock,
                log=log,
            )
            return True
        authorization = result.recovery_authorization
        wait = result.recovery_wait
        if authorization is not None:
            recovery_operations += 1
            recovery_index += 1
            recovery_step_id = f"{step_id}-recovery-{recovery_index:03d}"
            _log_recovery_command(log, recovery_step_id, authorization, clock())
            try:
                status = adapter.apply(authorization)
            except Exception as exc:
                unavailable = supervisor.recovery_unavailable(type(exc).__name__)
                log.transition(
                    step_id=recovery_step_id,
                    operation="recovery-unavailable",
                    result=unavailable,
                    monotonic_ns=clock(),
                )
                break
            _log_status(log, recovery_step_id, status, clock())
            controller_decision = _controller_settling_decision(
                status=status,
                request=authorization.request,
                supervisor=supervisor,
                now_ns=clock(),
            )
            log.append(
                "controller-settling.jsonl",
                {
                    "run_id": config.run_id,
                    "step_id": recovery_step_id,
                    "decision": controller_decision.model_dump(mode="json"),
                },
            )
            result = supervisor.record_status(status)
            log.transition(
                step_id=recovery_step_id,
                operation="record-recovery-status",
                result=result,
                monotonic_ns=clock(),
            )
            if clock() >= recovery_deadline_ns:
                _exhaust_recovery(
                    step_id=step_id,
                    supervisor=supervisor,
                    clock=clock,
                    log=log,
                )
                return True
            if result.state is RunState.FAULTED and supervisor.safe_state_verified:
                _verify_recovery_home(
                    config=config,
                    observer=observer,
                    baseline=baseline,
                    step_id=recovery_step_id,
                    status_ns=status.reported_monotonic_ns,
                    supervisor=supervisor,
                    clock=clock,
                    sleeper=sleeper,
                    log=log,
                    deadline_ns=recovery_deadline_ns,
                )
            continue
        if wait is not None and wait.reason is RecoveryWaitReason.MOTION_LIMITS:
            remaining_ns = wait.retry_not_before_monotonic_ns - clock()
            if wait.retry_not_before_monotonic_ns >= recovery_deadline_ns:
                _exhaust_recovery(
                    step_id=step_id,
                    supervisor=supervisor,
                    clock=clock,
                    log=log,
                )
                return True
            if remaining_ns > 0:
                try:
                    sleeper(remaining_ns / 1_000_000_000)
                except Exception as exc:
                    unavailable = supervisor.recovery_unavailable(
                        f"recovery sleeper failed: {type(exc).__name__}"
                    )
                    log.transition(
                        step_id=f"{step_id}-recovery-sleeper-error",
                        operation="recovery-unavailable",
                        result=unavailable,
                        monotonic_ns=clock(),
                    )
                    return False
            recovery_operations += 1
            result = supervisor.retry_recovery(clock())
            log.transition(
                step_id=f"{step_id}-recovery-wait",
                operation="retry-recovery",
                result=result,
                monotonic_ns=clock(),
            )
            continue
        # Reconciliation/operator/communication waits require evidence which this
        # mock runner does not possess. Never reinterpret their absence as Home.
        unavailable = supervisor.recovery_unavailable(
            "runner has no independent reconciliation evidence"
        )
        log.transition(
            step_id=f"{step_id}-recovery-unavailable",
            operation="recovery-unavailable",
            result=unavailable,
            monotonic_ns=clock(),
        )
        break
    return False


def _exhaust_recovery(
    *,
    step_id: str,
    supervisor: SafetySupervisor,
    clock: Callable[[], int],
    log: _RunLog,
) -> None:
    log.fault(
        step_id=f"{step_id}-recovery-exhausted",
        fault=SafetyFault(
            code="recovery-exhausted",
            detail="configured recovery deadline or attempt count was exhausted",
            occurred_monotonic_ns=clock(),
        ),
    )
    if supervisor.state is RunState.ABORTING:
        unavailable = supervisor.recovery_unavailable(
            "configured recovery deadline or attempt count exhausted"
        )
        log.transition(
            step_id=f"{step_id}-recovery-exhausted",
            operation="recovery-unavailable",
            result=unavailable,
            monotonic_ns=clock(),
        )


def _verify_recovery_home(
    *,
    config: IdentificationConfig,
    observer: IdentificationObserver,
    baseline: VisualHomeBaseline | None,
    step_id: str,
    status_ns: int,
    supervisor: SafetySupervisor,
    clock: Callable[[], int],
    sleeper: Callable[[float], object],
    log: _RunLog,
    deadline_ns: int,
) -> None:
    """Record independent visual Home evidence after controller-confirmed recovery."""

    if not _recovery_visual_sleep(
        config.controller_settle_ms / 1_000,
        step_id=step_id,
        sleeper=sleeper,
        clock=clock,
        log=log,
    ):
        return
    if clock() >= deadline_ns:
        _log_recovery_visual_deadline(step_id=step_id, clock=clock, log=log)
        return
    if not _recovery_visual_sleep(
        config.visual_settle_ms / 1_000,
        step_id=step_id,
        sleeper=sleeper,
        clock=clock,
        log=log,
    ):
        return
    if clock() >= deadline_ns:
        _log_recovery_visual_deadline(step_id=step_id, clock=clock, log=log)
        return
    observations: list[BlendshapeObservation] = []
    try:
        for sample_index in range(config.samples_per_step):
            if sample_index:
                if not _recovery_visual_sleep(
                    config.sample_interval_ms / 1_000,
                    step_id=step_id,
                    sleeper=sleeper,
                    clock=clock,
                    log=log,
                ):
                    return
                if clock() >= deadline_ns:
                    raise _ControlledAbort(
                        "recovery-home-deadline-exceeded",
                        "recovery visual verification exceeded its deadline",
                        category=FailureCategory.RECOVERY_ERROR,
                    )
            try:
                item = observer.observe(run_id=config.run_id, step_id=step_id)
            except Exception as exc:
                raise _ControlledAbort(
                    "observer-error",
                    type(exc).__name__,
                    category=FailureCategory.CAMERA_LOSS,
                    camera_loss=True,
                ) from exc
            if clock() >= deadline_ns:
                raise _ControlledAbort(
                    "recovery-home-deadline-exceeded",
                    "recovery observer exceeded its deadline",
                    category=FailureCategory.RECOVERY_ERROR,
                )
            _validate_observation(item, config=config, status_ns=status_ns)
            observations.append(item)
            log.append(
                "observations.jsonl",
                {
                    "run_id": config.run_id,
                    "step_id": step_id,
                    "sample_index": sample_index,
                    "observation": item.model_dump(mode="json"),
                },
            )
        if baseline is None:
            raise _ControlledAbort(
                "recovery-home-baseline-missing",
                "no initial visual Home baseline exists",
                category=FailureCategory.VISUAL_ERROR,
            )
        recovery_step = IdentificationStep(
            step_id=step_id,
            actuator_name=(
                supervisor.manifest.actuators[0].name
                if not supervisor.committed_targets
                else next(iter(supervisor.committed_targets))
            ),
            normalized_position=0.0,
            phase="home",
        )
        visual_decision, _ = _visual_decision(
            config=config,
            step=recovery_step,
            observations=observations,
            baseline=baseline,
            now_ns=clock(),
        )
        verified = visual_decision.home_verified is True
        log.append(
            "visual-settling.jsonl",
            {
                "run_id": config.run_id,
                "step_id": step_id,
                "decision": visual_decision.model_dump(mode="json"),
            },
        )
    except _ControlledAbort as error:
        verified = False
        log.fault(
            step_id=step_id,
            fault=SafetyFault(
                code="recovery-home-observation-unavailable",
                detail=error.code,
                occurred_monotonic_ns=clock(),
            ),
        )
    log.append(
        "transitions.jsonl",
        {
            "run_id": config.run_id,
            "step_id": step_id,
            "operation": "home-verified",
            "monotonic_ns": clock(),
            "accepted": verified,
            "state": supervisor.state.value,
        },
    )


def _log_recovery_visual_deadline(
    *, step_id: str, clock: Callable[[], int], log: _RunLog
) -> None:
    log.fault(
        step_id=step_id,
        fault=SafetyFault(
            code="recovery-home-deadline-exceeded",
            detail="recovery visual verification exceeded its deadline",
            occurred_monotonic_ns=clock(),
        ),
    )
    log.append(
        "transitions.jsonl",
        {
            "run_id": log.run_id,
            "step_id": step_id,
            "operation": "home-verified",
            "monotonic_ns": clock(),
            "accepted": False,
            "state": RunState.FAULTED.value,
        },
    )


def _recovery_visual_sleep(
    seconds: float,
    *,
    step_id: str,
    sleeper: Callable[[float], object],
    clock: Callable[[], int],
    log: _RunLog,
) -> bool:
    try:
        sleeper(seconds)
    except Exception as exc:
        log.fault(
            step_id=step_id,
            fault=SafetyFault(
                code="recovery-sleeper-error",
                detail=type(exc).__name__,
                occurred_monotonic_ns=clock(),
            ),
        )
        log.append(
            "transitions.jsonl",
            {
                "run_id": log.run_id,
                "step_id": step_id,
                "operation": "home-verified",
                "monotonic_ns": clock(),
                "accepted": False,
                "state": RunState.FAULTED.value,
            },
        )
        return False
    return True


def _controller_settling_decision(
    *,
    status: ActuatorStatus,
    request: PoseRequest,
    supervisor: SafetySupervisor,
    now_ns: int,
) -> ControllerSettlingDecision:
    last_by_actuator: dict[str, ControllerOutputSample] = {}
    for sample in status.controller_output_samples:
        previous = last_by_actuator.get(sample.actuator_name)
        if (
            previous is None
            or sample.observed_monotonic_ns >= previous.observed_monotonic_ns
        ):
            last_by_actuator[sample.actuator_name] = sample
    reached = status.targets_reached is True
    for target in request.targets:
        expected_qus = supervisor.manifest.actuator(target.actuator_name).target_qus(
            target.normalized_position
        )
        terminal_sample = last_by_actuator.get(target.actuator_name)
        if (
            terminal_sample is None
            or terminal_sample.target_qus != expected_qus
            or terminal_sample.observed_qus != expected_qus
        ):
            reached = False
    return ControllerSettlingDecision(
        target_reached=reached,
        requested_targets=request.targets,
        samples=status.controller_output_samples,
        decided_monotonic_ns=now_ns,
    )


def _log_command(
    log: _RunLog,
    *,
    step: IdentificationStep,
    authorization: AuthorizationDecision,
    kind: str,
    clock: Callable[[], int],
) -> None:
    assert authorization.request is not None
    log.append(
        "commands.jsonl",
        {
            "run_id": log.run_id,
            "step_id": step.step_id,
            "phase": step.phase,
            "actuator_name": step.actuator_name,
            "normalized_position": step.normalized_position,
            "authorization_kind": kind,
            "monotonic_ns": clock(),
            "request": authorization.request.model_dump(mode="json"),
        },
    )


def _log_recovery_command(
    log: _RunLog,
    step_id: str,
    authorization: RecoveryAuthorization,
    now_ns: int,
) -> None:
    target = authorization.request.targets[0]
    log.append(
        "commands.jsonl",
        {
            "run_id": log.run_id,
            "step_id": step_id,
            "phase": "recovery",
            "actuator_name": target.actuator_name,
            "normalized_position": target.normalized_position,
            "authorization_kind": "recovery",
            "monotonic_ns": now_ns,
            "recovery_sequence_index": authorization.sequence_index,
            "originating_fault_code": authorization.originating_fault_code,
            "request": authorization.request.model_dump(mode="json"),
        },
    )


def _log_status(log: _RunLog, step_id: str, status: Any, now_ns: int) -> None:
    log.append(
        "statuses.jsonl",
        {
            "run_id": log.run_id,
            "step_id": step_id,
            "monotonic_ns": now_ns,
            "status": status.model_dump(mode="json"),
        },
    )


def _artifact_payloads(
    *, config: IdentificationConfig, log: _RunLog, status: RunStatus
) -> dict[str, bytes]:
    payloads = {
        name: b"".join(_json_bytes(record) for record in records)
        for name, records in log.records.items()
    }
    payloads["metrics.json"] = _json_bytes(
        {
            "schema_version": "identification-metrics-placeholder/v1",
            "run_id": config.run_id,
            "state": "pending_analysis",
            "run_status": status.value,
        },
        indent=2,
    )
    payloads["conclusion.md"] = (
        "# Actuator identification conclusion\n\n"
        "State: pending analysis. No Phase 2 acceptance claim has been made.\n"
    ).encode()
    return payloads


def _json_bytes(value: Any, *, indent: int | None = None) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=indent) + "\n").encode()


def _record_for_bytes(name: str, payload: bytes) -> ArtifactRecord:
    return ArtifactRecord(
        path=name,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def _validate_new_output(output_dir: Path) -> None:
    if output_dir.exists():
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise FileExistsError(f"output_dir must be absent or empty: {output_dir}")
        output_dir.rmdir()


def _safe_error_type(value: str) -> str:
    normalized = "".join(part.title() for part in value.replace("_", "-").split("-"))
    return normalized or "IdentificationAbort"


def _git_revision() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None
