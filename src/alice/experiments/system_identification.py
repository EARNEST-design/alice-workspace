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

from alice.contracts.actuation import ActuatorTarget, PoseRequest
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
    NegotiatedCameraSettings,
    RunStatus,
)
from alice.hardware.adapter import ActuatorAdapter
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


class IdentificationConfig(BaseModel):
    """Reviewed mock-run configuration; hardware selection is unrepresentable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["identification-config/v1"]
    run_id: NonEmptyString
    adapter: Literal["mock"]
    hardware_id: NonEmptyString
    calibration_sha256: Sha256Hex
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
    random_seeds: tuple[int, ...]
    provenance: Mapping[NonEmptyString, NonEmptyString]
    retention: Literal["derived_observations_only"]

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
    def __init__(self, code: str, detail: str, *, camera_loss: bool = False) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.camera_loss = camera_loss


def run_identification(
    config: IdentificationConfig,
    observer: IdentificationObserver,
    supervisor: SafetySupervisor,
    adapter: ActuatorAdapter,
    output_dir: Path,
    clock: Callable[[], int],
    sleeper: Callable[[float], object],
) -> ArtifactManifest:
    """Run the conservative mock sequence and atomically publish its evidence."""

    _validate_new_output(output_dir)
    started_at = datetime.now(UTC)
    log = _RunLog(config.run_id)
    status = RunStatus.COMPLETED
    failure: FailureRecord | None = None
    aborted_reason: str | None = None
    current_step_id = "run-start"

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
            category=FailureCategory.CAPTURE_ERROR,
            error_type="SafetyStartRejected",
        )
        aborted_reason = "identification aborted: supervisor start rejected"
    else:
        try:
            for step in _steps(config):
                current_step_id = step.step_id
                _execute_step(
                    config=config,
                    step=step,
                    observer=observer,
                    supervisor=supervisor,
                    adapter=adapter,
                    clock=clock,
                    sleeper=sleeper,
                    log=log,
                )
        except _ControlledAbort as error:
            status = RunStatus.ABORTED
            failure = FailureRecord(
                category=(
                    FailureCategory.OBSERVER_ERROR
                    if error.camera_loss
                    else FailureCategory.CAPTURE_ERROR
                ),
                error_type=_safe_error_type(error.code),
            )
            aborted_reason = f"identification aborted: {error.code}"
            _abort_and_recover(
                error=error,
                step_id=current_step_id,
                config=config,
                observer=observer,
                supervisor=supervisor,
                adapter=adapter,
                clock=clock,
                sleeper=sleeper,
                log=log,
            )

    files = _artifact_payloads(config=config, log=log, status=status)
    artifacts = {
        name: _record_for_bytes(name, payload) for name, payload in files.items()
    }
    manifest = ArtifactManifest(
        schema_version="artifact-manifest/v1",
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
        camera_settings=NegotiatedCameraSettings.unavailable(),
        aborted_reason=aborted_reason,
        failure=failure,
        conclusion=None,
    )
    files["manifest.json"] = _json_bytes(manifest.model_dump(mode="json"), indent=2)
    publish_generation(output_dir.parent.resolve(), output_dir.name, files)
    return manifest


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
    observer: IdentificationObserver,
    supervisor: SafetySupervisor,
    adapter: ActuatorAdapter,
    clock: Callable[[], int],
    sleeper: Callable[[float], object],
    log: _RunLog,
) -> None:
    step_started_ns = clock()
    sleeper(config.command_interval_ms / 1_000)
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
        raise _ControlledAbort("authorization-rejected", "supervisor rejected step")
    _log_command(log, step=step, authorization=decision, kind="normal", clock=clock)
    try:
        actuator_status = adapter.apply(decision)
    except Exception as exc:
        raise _ControlledAbort("adapter-error", type(exc).__name__) from exc
    _log_status(log, step.step_id, actuator_status, clock())
    recorded = supervisor.record_status(actuator_status)
    log.transition(
        step_id=step.step_id,
        operation="record-status",
        result=recorded,
        monotonic_ns=clock(),
    )
    if not recorded.accepted or recorded.state is not RunState.RUNNING:
        raise _ControlledAbort("controller-fault", "controller status was not accepted")

    sleeper(config.controller_settle_ms / 1_000)
    sleeper(config.visual_settle_ms / 1_000)
    _check_timeout(config, step_started_ns, clock())
    observations: list[BlendshapeObservation] = []
    for sample_index in range(config.samples_per_step):
        if sample_index:
            sleeper(config.sample_interval_ms / 1_000)
        _check_timeout(config, step_started_ns, clock())
        try:
            item = observer.observe(run_id=config.run_id, step_id=step.step_id)
        except Exception as exc:
            raise _ControlledAbort(
                "observer-error", type(exc).__name__, camera_loss=True
            ) from exc
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
    variance = _maximum_variance(observations)
    if variance > config.maximum_visual_variance:
        raise _ControlledAbort(
            "visual-variance-breach",
            f"maximum variance {variance} exceeds configured threshold",
        )
    if step.phase == "home":
        log.append(
            "transitions.jsonl",
            {
                "run_id": config.run_id,
                "step_id": step.step_id,
                "operation": "home-verified",
                "monotonic_ns": clock(),
                "accepted": True,
                "state": supervisor.state.value,
            },
        )


def _validate_observation(
    observation: BlendshapeObservation,
    *,
    config: IdentificationConfig,
    status_ns: int,
) -> None:
    if observation.run_id != config.run_id:
        raise _ControlledAbort("observation-run-mismatch", "wrong observation run")
    if observation.validity is not ObservationValidity.VALID:
        raise _ControlledAbort(
            "camera-loss",
            "observer did not return a face",
            camera_loss=True,
        )
    if observation.monotonic_ns < status_ns:
        raise _ControlledAbort(
            "stale-observation", "observation predates controller status"
        )


def _check_timeout(config: IdentificationConfig, start_ns: int, now_ns: int) -> None:
    if now_ns - start_ns >= config.step_timeout_ms * 1_000_000:
        raise _ControlledAbort("step-timeout", "step exceeded its configured timeout")


def _maximum_variance(observations: list[BlendshapeObservation]) -> float:
    schemas = [tuple(score.name for score in item.scores) for item in observations]
    if len(set(schemas)) != 1:
        raise _ControlledAbort("blendshape-schema-mismatch", "score schema changed")
    by_name: dict[str, list[float]] = {name: [] for name in schemas[0]}
    for item in observations:
        for score in item.scores:
            by_name[score.name].append(score.score)
    return max(
        (statistics.pvariance(values) for values in by_name.values()), default=0.0
    )


def _abort_and_recover(
    *,
    error: _ControlledAbort,
    step_id: str,
    config: IdentificationConfig,
    observer: IdentificationObserver,
    supervisor: SafetySupervisor,
    adapter: ActuatorAdapter,
    clock: Callable[[], int],
    sleeper: Callable[[float], object],
    log: _RunLog,
) -> None:
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
        return

    recovery_index = 0
    while supervisor.state is RunState.ABORTING:
        authorization = result.recovery_authorization
        wait = result.recovery_wait
        if authorization is not None:
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
            result = supervisor.record_status(status)
            log.transition(
                step_id=recovery_step_id,
                operation="record-recovery-status",
                result=result,
                monotonic_ns=clock(),
            )
            if result.state is RunState.FAULTED and supervisor.safe_state_verified:
                _verify_recovery_home(
                    config=config,
                    observer=observer,
                    step_id=recovery_step_id,
                    status_ns=status.reported_monotonic_ns,
                    supervisor=supervisor,
                    clock=clock,
                    sleeper=sleeper,
                    log=log,
                )
            continue
        if wait is not None and wait.reason is RecoveryWaitReason.MOTION_LIMITS:
            remaining_ns = wait.retry_not_before_monotonic_ns - clock()
            if remaining_ns > 0:
                sleeper(remaining_ns / 1_000_000_000)
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


def _verify_recovery_home(
    *,
    config: IdentificationConfig,
    observer: IdentificationObserver,
    step_id: str,
    status_ns: int,
    supervisor: SafetySupervisor,
    clock: Callable[[], int],
    sleeper: Callable[[float], object],
    log: _RunLog,
) -> None:
    """Record independent visual Home evidence after controller-confirmed recovery."""

    sleeper(config.controller_settle_ms / 1_000)
    sleeper(config.visual_settle_ms / 1_000)
    observations: list[BlendshapeObservation] = []
    try:
        for sample_index in range(config.samples_per_step):
            if sample_index:
                sleeper(config.sample_interval_ms / 1_000)
            try:
                item = observer.observe(run_id=config.run_id, step_id=step_id)
            except Exception as exc:
                raise _ControlledAbort(
                    "observer-error", type(exc).__name__, camera_loss=True
                ) from exc
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
        variance = _maximum_variance(observations)
        verified = variance <= config.maximum_visual_variance
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
