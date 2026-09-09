"""Deterministic, hardware-independent actuator safety state machine."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from alice.contracts.actuation import (
    ActuatorStatus,
    ActuatorStatusState,
    ActuatorTarget,
    PoseRequest,
)
from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.hardware.manifest import HardwareManifest
from alice.safety.permits import (
    ActuationPermit,
    ActuationPermitVerifier,
    PermitKind,
    _PermitRegistry,
)


class RunState(StrEnum):
    DISARMED = "disarmed"
    PREFLIGHT = "preflight"
    ARMED = "armed"
    RUNNING = "running"
    ABORTING = "aborting"
    FAULTED = "faulted"


class AbortReason(StrEnum):
    OPERATOR_REQUEST = "operator-request"
    CAMERA_LOSS = "camera-loss"


class RecoveryWaitReason(StrEnum):
    MOTION_LIMITS = "motion-limits"
    POSITION_RECONCILIATION_REQUIRED = "position-reconciliation-required"
    OPERATOR_INTERVENTION_REQUIRED = "operator-intervention-required"
    COMMUNICATION_UNAVAILABLE = "communication-unavailable"


class SafetyLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_step: Annotated[float, Field(gt=0.0, le=2.0, allow_inf_nan=False)]
    max_rate_per_second: Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
    max_acceleration_per_second_squared: Annotated[
        float, Field(gt=0.0, allow_inf_nan=False)
    ]
    watchdog_timeout_ns: Annotated[int, Field(gt=0)]
    approval_max_age_ns: Annotated[int, Field(gt=0)]
    preflight_max_age_ns: Annotated[int, Field(gt=0)]
    command_max_age_ns: Annotated[int, Field(gt=0)]
    recovery_command_ttl_ns: Annotated[int, Field(gt=0)]


class PreflightEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: NonEmptyString
    hardware_id: NonEmptyString
    calibration_sha256: Sha256Hex
    controller_serial: NonEmptyString
    requirement_results: Mapping[NonEmptyString, bool]
    competing_process_detected: bool
    controller_error_codes: tuple[Annotated[int, Field(ge=0)], ...]
    home_verified: bool
    observed_monotonic_ns: Annotated[int, Field(ge=0)]
    observed_targets: tuple[ActuatorTarget, ...] = ()


class OperatorApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: NonEmptyString
    run_id: NonEmptyString
    confirmed_monotonic_ns: Annotated[int, Field(ge=0)]


class SafetyFault(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: NonEmptyString
    detail: NonEmptyString
    occurred_monotonic_ns: Annotated[int, Field(ge=0)]


class RecoveryWait(BaseModel):
    """A fail-closed recovery state that cannot yet emit a command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: RecoveryWaitReason
    detail: NonEmptyString
    retry_not_before_monotonic_ns: Annotated[int, Field(ge=0)]


class RecoveryAuthorization(BaseModel):
    """Supervisor authority and correlation for one recovery command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence_index: Annotated[int, Field(gt=0)]
    originating_fault_code: NonEmptyString
    request: PoseRequest
    permit: ActuationPermit


class TransitionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    state: RunState
    fault: SafetyFault | None = None
    recovery_request: PoseRequest | None = None
    recovery_authorization: RecoveryAuthorization | None = None
    recovery_wait: RecoveryWait | None = None


class AuthorizationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorized: bool
    state: RunState
    request: PoseRequest | None = None
    permit: ActuationPermit | None = None
    fault: SafetyFault | None = None
    recovery_request: PoseRequest | None = None
    recovery_authorization: RecoveryAuthorization | None = None
    recovery_wait: RecoveryWait | None = None


class SafetySupervisor:
    """Final policy authority before an injected actuator adapter.

    Authorization is not application. Controller command-state, velocity, and
    watchdog history advance only after a matching ``APPLIED`` status is
    recorded. Mechanical Home and visual settling remain independent preflight
    and experiment-runner responsibilities.
    """

    def __init__(
        self,
        *,
        manifest: HardwareManifest,
        limits: SafetyLimits,
        clock: Callable[[], int],
        allow_measured_start: bool = False,
    ) -> None:
        self._manifest = manifest
        self._limits = limits
        self._clock = clock
        self._allow_measured_start = allow_measured_start
        self._permit_registry = _PermitRegistry()
        self._state = RunState.DISARMED
        self._fault: SafetyFault | None = None
        self._fault_history: list[SafetyFault] = []
        self._preflight: PreflightEvidence | None = None
        self._approval: OperatorApproval | None = None
        self._run_id: str | None = None
        self._pending_request: PoseRequest | None = None
        self._recovery_request: PoseRequest | None = None
        self._recovery_authorization: RecoveryAuthorization | None = None
        self._recovery_wait: RecoveryWait | None = None
        self._originating_fault_code: str | None = None
        self._unknown_actuators: set[str] = set()
        self._home_confirmation_issued = False
        self._recovery_counter = 0
        self._safe_state_verified = False
        self._committed_targets = {
            actuator.name: 0.0 for actuator in self._manifest.actuators
        }
        self._committed_velocities = {
            actuator.name: 0.0 for actuator in self._manifest.actuators
        }
        self._last_applied_by_actuator: dict[str, int] = {}
        self._last_acknowledged_ns: int | None = None

    @property
    def actuation_permit_verifier(self) -> ActuationPermitVerifier:
        """Return consume-only authority for dependency-injected adapters."""

        return self._permit_registry.consumer

    @property
    def manifest(self) -> HardwareManifest:
        return self._manifest

    @property
    def limits(self) -> SafetyLimits:
        return self._limits

    @property
    def preflight_evidence(self) -> PreflightEvidence | None:
        return self._preflight

    @property
    def operator_approval(self) -> OperatorApproval | None:
        return self._approval

    @property
    def state(self) -> RunState:
        return self._state

    @property
    def fault(self) -> SafetyFault | None:
        return self._fault

    @property
    def fault_history(self) -> tuple[SafetyFault, ...]:
        return tuple(self._fault_history)

    @property
    def pending_request(self) -> PoseRequest | None:
        return self._pending_request

    @property
    def recovery_request(self) -> PoseRequest | None:
        return self._recovery_request if self._recovery_wait is None else None

    @property
    def recovery_authorization(self) -> RecoveryAuthorization | None:
        return self._recovery_authorization if self._recovery_wait is None else None

    @property
    def recovery_wait(self) -> RecoveryWait | None:
        return self._recovery_wait

    @property
    def committed_targets(self) -> Mapping[str, float]:
        return MappingProxyType(self._committed_targets)

    @property
    def last_applied_monotonic_ns(self) -> int | None:
        if not self._last_applied_by_actuator:
            return None
        return max(self._last_applied_by_actuator.values())

    @property
    def last_applied_by_actuator(self) -> Mapping[str, int]:
        return MappingProxyType(self._last_applied_by_actuator)

    @property
    def safe_state_verified(self) -> bool:
        return self._safe_state_verified

    def preflight(self, evidence: PreflightEvidence) -> TransitionResult:
        if self._state is not RunState.DISARMED:
            return self._invalid_transition("preflight", RunState.DISARMED)
        problem = self._preflight_problem(evidence, self._clock())
        if problem is not None:
            return self._enter_faulted(*problem)
        self._preflight = evidence
        self._run_id = evidence.run_id
        self._state = RunState.PREFLIGHT
        return TransitionResult(accepted=True, state=self._state)

    def arm(self, approval: OperatorApproval | None) -> TransitionResult:
        if self._state is not RunState.PREFLIGHT:
            return self._invalid_transition("arm", RunState.PREFLIGHT)
        now_ns = self._clock()
        assert self._preflight is not None
        problem = self._preflight_problem(self._preflight, now_ns)
        if problem is not None:
            return self._enter_faulted(*problem)
        if approval is None:
            return self._enter_faulted(
                "approval-missing", "operator approval is required"
            )
        approval_problem = self._approval_problem(approval, now_ns)
        if approval_problem is not None:
            return self._enter_faulted(*approval_problem)
        self._approval = approval
        self._last_applied_by_actuator = {
            actuator.name: self._preflight.observed_monotonic_ns
            for actuator in self._manifest.actuators
        }
        self._last_acknowledged_ns = self._preflight.observed_monotonic_ns
        assert self._preflight is not None
        self._committed_targets = (
            {
                t.actuator_name: t.normalized_position
                for t in self._preflight.observed_targets
            }
            if self._preflight.observed_targets
            else {actuator.name: 0.0 for actuator in self._manifest.actuators}
        )
        self._committed_velocities = {
            actuator.name: 0.0 for actuator in self._manifest.actuators
        }
        self._state = RunState.ARMED
        return TransitionResult(accepted=True, state=self._state)

    def start(self) -> TransitionResult:
        if self._state is not RunState.ARMED:
            return self._invalid_transition("start", RunState.ARMED)
        problem = self._runtime_gate_problem(self._clock())
        if problem is not None:
            return self._begin_abort(*problem)
        now_ns = self._clock()
        self._state = RunState.RUNNING
        self._last_applied_by_actuator = {
            actuator.name: now_ns for actuator in self._manifest.actuators
        }
        self._last_acknowledged_ns = now_ns
        return TransitionResult(accepted=True, state=self._state)

    def authorize(self, request: PoseRequest) -> AuthorizationDecision:
        if self._state is not RunState.RUNNING:
            rejected = self._invalid_transition("authorize", RunState.RUNNING)
            return AuthorizationDecision(
                authorized=False, state=self._state, fault=rejected.fault
            )
        now_ns = self._clock()
        problem = self._runtime_gate_problem(now_ns)
        if problem is not None:
            return self._abort_authorization(*problem)
        if self._pending_request is not None:
            return self._abort_authorization(
                "request-in-flight",
                "previous request lacks a matching APPLIED status",
            )
        if request.run_id != self._run_id:
            return self._abort_authorization(
                "run-identity-mismatch", "request is for a different run"
            )
        request_problem = self._request_problem(request, now_ns)
        if request_problem is not None:
            return self._abort_authorization(*request_problem)
        motion_problem = self._motion_problem(request, now_ns)
        if motion_problem is not None:
            return self._abort_authorization(*motion_problem)
        self._pending_request = request
        permit = self._permit_registry.issue(request=request, kind=PermitKind.NORMAL)
        return AuthorizationDecision(
            authorized=True,
            state=self._state,
            request=request,
            permit=permit,
        )

    def record_status(self, status: ActuatorStatus) -> TransitionResult:
        # End outstanding authority even if a caller submits status without
        # first passing through the adapter's consume-only permit verifier.
        self._permit_registry.revoke_all()
        if self._state is RunState.ABORTING:
            return self._record_recovery_status(status)
        if self._state is not RunState.RUNNING:
            return self._invalid_transition("record_status", RunState.RUNNING)
        if self._pending_request is None:
            return self._begin_abort(
                "unexpected-status",
                "actuator status arrived without a pending request",
            )
        pending = self._pending_request
        problem = self._status_problem(status, pending)
        if problem is not None:
            return self._begin_abort(*problem)
        if status.state is not ActuatorStatusState.APPLIED:
            self._begin_abort(
                "controller-error", status.fault_code or "actuator adapter failed"
            )
            return self._record_reconciliation_status(status)
        if not self._targets_match(status.applied_targets, pending.targets):
            self._begin_abort(
                "applied-target-mismatch",
                "applied targets differ from authorized targets",
            )
            return self._record_reconciliation_status(status)
        self._commit_applied(pending, status.reported_monotonic_ns)
        self._pending_request = None
        return TransitionResult(accepted=True, state=self._state)

    def watchdog(self, now_ns: int) -> TransitionResult:
        if self._state is not RunState.RUNNING:
            return self._invalid_transition("watchdog", RunState.RUNNING)
        clock_now_ns = self._clock()
        if now_ns != clock_now_ns:
            return self._begin_abort(
                "watchdog-time-mismatch",
                "watchdog timestamp does not match the injected monotonic clock",
            )
        problem = self._runtime_gate_problem(clock_now_ns)
        if problem is not None:
            return self._begin_abort(*problem)
        assert self._last_acknowledged_ns is not None
        if now_ns - self._last_acknowledged_ns >= self._limits.watchdog_timeout_ns:
            return self._begin_abort(
                "watchdog-expired",
                "no matching APPLIED status arrived before the watchdog deadline",
            )
        return TransitionResult(accepted=True, state=self._state)

    def abort(
        self,
        reason: AbortReason,
        *,
        communication_available: bool = True,
    ) -> TransitionResult:
        if self._state not in (RunState.ARMED, RunState.RUNNING):
            return self._invalid_transition("abort", RunState.RUNNING)
        return self._begin_abort(
            reason.value,
            f"runtime abort requested: {reason.value}",
            communication_available=communication_available,
        )

    def complete_run(self) -> TransitionResult:
        """Leave RUNNING only after exact controller-command Home is confirmed."""

        if self._state is not RunState.RUNNING:
            return self._invalid_transition("complete_run", RunState.RUNNING)
        if self._pending_request is not None:
            return self._begin_abort(
                "complete-request-in-flight",
                "cannot complete with an unacknowledged actuator request",
            )
        if any(abs(value) > 1e-12 for value in self._committed_targets.values()):
            return self._begin_abort(
                "complete-not-home",
                "cannot complete until every controller-command target is Home",
            )
        self._permit_registry.revoke_all()
        self._state = RunState.DISARMED
        return TransitionResult(accepted=True, state=self._state)

    def cancel_armed(self, reason: str) -> TransitionResult:
        """Cancel pre-run authority without issuing or implying any motion."""

        if self._state is not RunState.ARMED:
            return self._invalid_transition("cancel_armed", RunState.ARMED)
        if not reason.strip():
            fault = self._make_fault(
                "cancellation-reason-missing",
                "pre-run cancellation requires a recorded reason",
            )
            return TransitionResult(accepted=False, state=self._state, fault=fault)
        self._reset_disarmed()
        return TransitionResult(accepted=True, state=self._state)

    def complete_abort(self) -> TransitionResult:
        if self._state is not RunState.ABORTING:
            return self._invalid_transition("complete_abort", RunState.ABORTING)
        fault = self._make_fault(
            "home-not-confirmed", "matching Home APPLIED status has not been recorded"
        )
        return self._recovery_result(accepted=False, fault=fault)

    def retry_recovery(self, now_ns: int) -> TransitionResult:
        if self._state is not RunState.ABORTING:
            return self._invalid_transition("retry_recovery", RunState.ABORTING)
        if now_ns != self._clock():
            fault = self._make_fault(
                "recovery-time-mismatch",
                "retry timestamp does not match the injected monotonic clock",
            )
            return self._recovery_result(accepted=False, fault=fault)
        if self._pending_request is not None or self._unknown_actuators:
            return self._recovery_result(accepted=False, fault=self._fault)
        if self._recovery_authorization is not None:
            assert self._recovery_request is not None
            if self._recovery_request.is_expired(now_monotonic_ns=now_ns):
                self._permit_registry.revoke_all()
                fault = self._record_fault(
                    "recovery-expired-unacknowledged",
                    "expired recovery application is unknown; status is required",
                )
                self._unknown_actuators = {
                    target.actuator_name for target in self._recovery_request.targets
                }
                self._recovery_wait = RecoveryWait(
                    reason=RecoveryWaitReason.POSITION_RECONCILIATION_REQUIRED,
                    detail="expired recovery command requires position reconciliation",
                    retry_not_before_monotonic_ns=now_ns,
                )
                return self._recovery_result(accepted=False, fault=fault)
            return self._recovery_result(accepted=False, fault=self._fault)
        return self._plan_recovery(accepted=False, fault=self._fault)

    def recovery_unavailable(self, detail: str) -> TransitionResult:
        if self._state is not RunState.ABORTING:
            return self._invalid_transition("recovery_unavailable", RunState.ABORTING)
        self._permit_registry.revoke_all()
        fault = self._record_fault("recovery-unavailable", detail)
        self._state = RunState.FAULTED
        self._safe_state_verified = False
        self._recovery_request = None
        self._recovery_authorization = None
        self._recovery_wait = None
        return TransitionResult(accepted=True, state=self._state, fault=fault)

    def revoke_external_authority(self, detail: str) -> TransitionResult:
        """Fail closed when an independent hardware authority detects a fault."""

        self._permit_registry.revoke_all()
        self._pending_request = None
        self._recovery_request = None
        self._recovery_authorization = None
        self._recovery_wait = None
        fault = self._record_fault("external-authority-revoked", detail)
        self._state = RunState.FAULTED
        self._safe_state_verified = False
        return TransitionResult(accepted=True, state=self._state, fault=fault)

    def acknowledge_fault(self, evidence: PreflightEvidence) -> TransitionResult:
        if self._state is not RunState.FAULTED:
            return self._invalid_transition("acknowledge_fault", RunState.FAULTED)
        problem = self._preflight_problem(evidence, self._clock())
        if problem is not None:
            fault = self._make_fault(*problem)
            return TransitionResult(accepted=False, state=self._state, fault=fault)
        self._reset_disarmed()
        return TransitionResult(accepted=True, state=self._state)

    def _runtime_gate_problem(self, now_ns: int) -> tuple[str, str] | None:
        if self._preflight is None:
            return "preflight-missing", "no retained preflight evidence"
        problem = self._preflight_problem(self._preflight, now_ns)
        if problem is not None:
            return problem
        if self._approval is None:
            return "approval-missing", "no retained operator approval"
        return self._approval_problem(self._approval, now_ns)

    def _preflight_problem(
        self, evidence: PreflightEvidence, now_ns: int
    ) -> tuple[str, str] | None:
        if evidence.observed_monotonic_ns > now_ns:
            return "preflight-issued-in-future", "preflight timestamp is in the future"
        if now_ns - evidence.observed_monotonic_ns >= self._limits.preflight_max_age_ns:
            return "preflight-stale", "preflight evidence exceeded its age limit"
        if evidence.hardware_id != self._manifest.hardware_id:
            return "hardware-identity-mismatch", "preflight hardware identity mismatch"
        if evidence.calibration_sha256 != self._manifest.calibration_sha256:
            return "calibration-mismatch", "preflight calibration identity mismatch"
        if evidence.controller_serial != self._manifest.controller.serial_number:
            return (
                "controller-identity-mismatch",
                "controller serial does not match manifest",
            )
        required = {
            requirement.requirement_id
            for requirement in self._manifest.preflight_requirements
        }
        failed = {
            name
            for name in required
            if evidence.requirement_results.get(name) is not True
        }
        if failed:
            return (
                "preflight-requirement-failed",
                f"unmet requirements: {sorted(failed)}",
            )
        if evidence.competing_process_detected:
            return "competing-process", "a competing actuator process was detected"
        if evidence.controller_error_codes:
            return "controller-error", "controller error register is non-zero"
        if evidence.observed_targets:
            if not self._allow_measured_start:
                return "measured-start-disabled", "this runner requires Home startup"
            names = [t.actuator_name for t in evidence.observed_targets]
            if len(names) != len(set(names)) or set(names) != {
                a.name for a in self._manifest.actuators
            }:
                return (
                    "invalid-start-targets",
                    "measured start must cover every actuator once",
                )
            if evidence.home_verified and any(
                t.normalized_position != 0 for t in evidence.observed_targets
            ):
                return (
                    "contradictory-start-targets",
                    "Home conflicts with observed targets",
                )
        elif not evidence.home_verified:
            return "home-not-verified", "reviewed Home positions were not verified"
        return None

    def _approval_problem(
        self, approval: OperatorApproval, now_ns: int
    ) -> tuple[str, str] | None:
        if approval.run_id != self._run_id:
            return "approval-run-mismatch", "approval is for a different run"
        if approval.confirmed_monotonic_ns > now_ns:
            return "approval-issued-in-future", "approval timestamp is in the future"
        if now_ns - approval.confirmed_monotonic_ns >= self._limits.approval_max_age_ns:
            return "approval-stale", "operator approval exceeded its age limit"
        return None

    def _motion_problem(
        self, request: PoseRequest, now_ns: int
    ) -> tuple[str, str] | None:
        for target in request.targets:
            name = target.actuator_name
            last_applied_ns = self._last_applied_by_actuator[name]
            elapsed_ns = now_ns - last_applied_ns
            if elapsed_ns <= 0:
                return (
                    "rate-limit-exceeded",
                    f"{name} movement has no elapsed applied-state time",
                )
            elapsed_seconds = elapsed_ns / 1_000_000_000
            delta = target.normalized_position - self._committed_targets[name]
            if abs(delta) > self._limits.max_step + 1e-12:
                return (
                    "step-limit-exceeded",
                    f"{name} step {abs(delta)} exceeds {self._limits.max_step}",
                )
            velocity = delta / elapsed_seconds
            if abs(velocity) > self._limits.max_rate_per_second:
                return (
                    "rate-limit-exceeded",
                    f"{name} rate {abs(velocity)} exceeds "
                    f"{self._limits.max_rate_per_second}",
                )
            acceleration = (
                abs(velocity - self._committed_velocities[name]) / elapsed_seconds
            )
            if acceleration > self._limits.max_acceleration_per_second_squared:
                return (
                    "acceleration-limit-exceeded",
                    f"{name} acceleration {acceleration} exceeds "
                    f"{self._limits.max_acceleration_per_second_squared}",
                )
        return None

    def _status_problem(
        self, status: ActuatorStatus, pending: PoseRequest
    ) -> tuple[str, str] | None:
        if not self._status_identity_matches(status, pending):
            return (
                "status-identity-mismatch",
                "actuator status does not match pending request",
            )
        now_ns = self._clock()
        if status.reported_monotonic_ns > now_ns:
            return (
                "status-issued-in-future",
                "actuator status timestamp is in the future",
            )
        if status.reported_monotonic_ns < pending.issued_monotonic_ns:
            return "status-before-request", "actuator status predates its request"
        if now_ns - status.reported_monotonic_ns >= self._limits.watchdog_timeout_ns:
            return "status-stale", "actuator status exceeded the watchdog age limit"
        return None

    def _record_recovery_status(self, status: ActuatorStatus) -> TransitionResult:
        if self._pending_request is not None and self._recovery_request is None:
            return self._record_reconciliation_status(status)
        recovery = self._recovery_request
        if recovery is None or not self._status_identity_matches(status, recovery):
            fault = self._record_fault(
                "recovery-status-mismatch",
                "status does not match the authorized recovery request",
            )
            return self._recovery_result(accepted=False, fault=fault)
        if recovery.is_expired(now_monotonic_ns=self._clock()):
            fault = self._record_fault(
                "recovery-expired",
                "recovery request expired before APPLIED status was recorded",
            )
            self._unknown_actuators = {
                target.actuator_name for target in recovery.targets
            }
            self._recovery_request = None
            self._recovery_authorization = None
            self._recovery_wait = RecoveryWait(
                reason=RecoveryWaitReason.OPERATOR_INTERVENTION_REQUIRED,
                detail="late recovery status cannot establish an authorized position",
                retry_not_before_monotonic_ns=self._clock(),
            )
            return self._recovery_result(accepted=False, fault=fault)
        problem = self._status_problem(status, recovery)
        if problem is not None:
            fault = self._record_fault(*problem)
            return self._recovery_result(accepted=False, fault=fault)
        if status.state is not ActuatorStatusState.APPLIED:
            fault = self._record_fault(
                "recovery-controller-error",
                status.fault_code or "recovery request was not applied",
            )
            self._recovery_request = None
            self._recovery_authorization = None
            if status.state is ActuatorStatusState.REJECTED:
                return self._plan_recovery(accepted=False, fault=fault)
            target_names = {target.actuator_name for target in recovery.targets}
            applied_names = {target.actuator_name for target in status.applied_targets}
            if applied_names == target_names and self._targets_match(
                status.applied_targets, recovery.targets
            ):
                self._commit_applied(recovery, status.reported_monotonic_ns)
                if all(value == 0.0 for value in self._committed_targets.values()):
                    self._safe_state_verified = True
                    self._state = RunState.FAULTED
                    return TransitionResult(
                        accepted=False, state=self._state, fault=fault
                    )
                return self._plan_recovery(accepted=False, fault=fault)
            self._unknown_actuators = target_names - applied_names
            if not self._unknown_actuators:
                self._unknown_actuators = target_names
            self._recovery_wait = RecoveryWait(
                reason=RecoveryWaitReason.OPERATOR_INTERVENTION_REQUIRED,
                detail="recovery fault left physical position uncertain",
                retry_not_before_monotonic_ns=self._clock(),
            )
            return self._recovery_result(accepted=False, fault=fault)
        if not self._targets_match(status.applied_targets, recovery.targets):
            fault = self._record_fault(
                "recovery-target-mismatch",
                "status does not report the authorized recovery target",
            )
            return self._recovery_result(accepted=False, fault=fault)
        self._commit_applied(recovery, status.reported_monotonic_ns)
        self._recovery_request = None
        self._recovery_authorization = None
        if all(value == 0.0 for value in self._committed_targets.values()):
            self._safe_state_verified = True
            self._state = RunState.FAULTED
            self._recovery_wait = None
            return TransitionResult(accepted=True, state=self._state, fault=self._fault)
        return self._plan_recovery(accepted=True, fault=self._fault)

    def _record_reconciliation_status(self, status: ActuatorStatus) -> TransitionResult:
        assert self._pending_request is not None
        pending = self._pending_request
        problem = self._status_problem(status, pending)
        if problem is not None:
            fault = self._record_fault(*problem)
            return self._recovery_result(accepted=False, fault=fault)
        requested = {target.actuator_name: target for target in pending.targets}
        applied = {target.actuator_name: target for target in status.applied_targets}
        if any(requested.get(name) != target for name, target in applied.items()):
            fault = self._record_fault(
                "reconciliation-target-mismatch",
                "status reports a target not present in the pending request",
            )
            return self._recovery_result(accepted=False, fault=fault)
        if status.state is ActuatorStatusState.REJECTED:
            self._unknown_actuators.clear()
        elif status.state is ActuatorStatusState.APPLIED and applied == requested:
            self._commit_applied(pending, status.reported_monotonic_ns)
            self._unknown_actuators.clear()
        elif status.state is ActuatorStatusState.FAULT:
            if applied:
                known_request = pending.model_copy(
                    update={"targets": tuple(applied.values())}
                )
                self._commit_applied(known_request, status.reported_monotonic_ns)
            self._unknown_actuators = set(requested) - set(applied)
            if not self._unknown_actuators:
                self._record_fault(
                    "controller-error",
                    status.fault_code or "pending request faulted after application",
                )
        else:
            self._unknown_actuators = set(requested)
        self._pending_request = None
        if self._unknown_actuators:
            fault = self._record_fault(
                "position-uncertain",
                f"unreconciled actuators: {sorted(self._unknown_actuators)}",
            )
            self._recovery_wait = RecoveryWait(
                reason=RecoveryWaitReason.OPERATOR_INTERVENTION_REQUIRED,
                detail=(
                    "physical position is not known; verify position or remove power"
                ),
                retry_not_before_monotonic_ns=self._clock(),
            )
            return self._recovery_result(accepted=False, fault=fault)
        return self._plan_recovery(accepted=True, fault=self._fault)

    def _begin_abort(
        self,
        code: str,
        detail: str,
        *,
        communication_available: bool = True,
    ) -> TransitionResult:
        self._permit_registry.revoke_all()
        fault = self._record_fault(code, detail)
        self._state = RunState.ABORTING
        self._safe_state_verified = False
        self._originating_fault_code = code
        self._recovery_request = None
        self._recovery_authorization = None
        self._recovery_wait = None
        if not communication_available:
            self._recovery_wait = RecoveryWait(
                reason=RecoveryWaitReason.COMMUNICATION_UNAVAILABLE,
                detail="no recovery motion is authorized without communication",
                retry_not_before_monotonic_ns=self._clock(),
            )
            return self._recovery_result(accepted=False, fault=fault)
        if self._pending_request is not None:
            self._unknown_actuators = {
                target.actuator_name for target in self._pending_request.targets
            }
            self._recovery_wait = RecoveryWait(
                reason=RecoveryWaitReason.POSITION_RECONCILIATION_REQUIRED,
                detail="pending request application is unknown; record its status",
                retry_not_before_monotonic_ns=self._clock(),
            )
            return self._recovery_result(accepted=False, fault=fault)
        return self._plan_recovery(accepted=False, fault=fault)

    def _abort_authorization(self, code: str, detail: str) -> AuthorizationDecision:
        result = self._begin_abort(code, detail)
        return AuthorizationDecision(
            authorized=False,
            state=result.state,
            fault=result.fault,
            recovery_request=result.recovery_request,
            recovery_authorization=result.recovery_authorization,
            recovery_wait=result.recovery_wait,
        )

    def _plan_recovery(
        self, *, accepted: bool, fault: SafetyFault | None
    ) -> TransitionResult:
        if self._unknown_actuators or self._pending_request is not None:
            return self._recovery_result(accepted=accepted, fault=fault)
        non_home = next(
            (
                actuator.name
                for actuator in self._manifest.actuators
                if self._committed_targets[actuator.name] != 0.0
            ),
            None,
        )
        actuator_name = non_home or self._manifest.actuators[0].name
        position = self._committed_targets[actuator_name]
        if position > 1e-12:
            target_position = max(0.0, position - self._limits.max_step)
        elif position < -1e-12:
            target_position = min(0.0, position + self._limits.max_step)
        else:
            target_position = 0.0
        if abs(target_position) <= 1e-12:
            target_position = 0.0
        else:
            target_position = round(target_position, 12)
        now_ns = self._clock()
        candidate = self._make_recovery_request(
            actuator_name=actuator_name,
            target_position=target_position,
            now_ns=now_ns,
        )
        request_problem = self._request_problem(candidate, now_ns)
        if request_problem is not None:
            recovery_fault = self._record_fault(
                "recovery-request-invalid",
                f"supervisor-created recovery failed validation: {request_problem[0]}",
            )
            self._recovery_wait = RecoveryWait(
                reason=RecoveryWaitReason.OPERATOR_INTERVENTION_REQUIRED,
                detail="recovery request validation failed closed",
                retry_not_before_monotonic_ns=now_ns,
            )
            return self._recovery_result(accepted=False, fault=recovery_fault)
        problem = self._motion_problem(candidate, now_ns)
        if problem is not None:
            retry_ns = self._find_safe_recovery_time(candidate, now_ns)
            if retry_ns is None:
                recovery_fault = self._record_fault(
                    "recovery-motion-unsatisfiable",
                    "no bounded retry time satisfied configured motion limits",
                )
                self._recovery_wait = RecoveryWait(
                    reason=RecoveryWaitReason.OPERATOR_INTERVENTION_REQUIRED,
                    detail="configured recovery motion limits require intervention",
                    retry_not_before_monotonic_ns=now_ns,
                )
                return self._recovery_result(accepted=False, fault=recovery_fault)
            self._recovery_wait = RecoveryWait(
                reason=RecoveryWaitReason.MOTION_LIMITS,
                detail=f"recovery waits for rate/acceleration limits: {problem[0]}",
                retry_not_before_monotonic_ns=retry_ns,
            )
            return self._recovery_result(accepted=accepted, fault=fault)
        self._recovery_counter += 1
        assert self._originating_fault_code is not None
        self._recovery_request = candidate
        self._recovery_authorization = RecoveryAuthorization(
            sequence_index=self._recovery_counter,
            originating_fault_code=self._originating_fault_code,
            request=candidate,
            permit=self._permit_registry.issue(
                request=candidate,
                kind=PermitKind.RECOVERY,
                recovery_sequence_index=self._recovery_counter,
                originating_fault_code=self._originating_fault_code,
            ),
        )
        self._recovery_wait = None
        return self._recovery_result(accepted=accepted, fault=fault)

    def _make_recovery_request(
        self, *, actuator_name: str, target_position: float, now_ns: int
    ) -> PoseRequest:
        assert self._run_id is not None
        return PoseRequest(
            schema_version="pose-request/v1",
            request_id=f"safety-recovery-{self._recovery_counter + 1}-{now_ns}",
            run_id=self._run_id,
            hardware_id=self._manifest.hardware_id,
            calibration_sha256=self._manifest.calibration_sha256,
            issued_monotonic_ns=now_ns,
            expires_monotonic_ns=now_ns + self._limits.recovery_command_ttl_ns,
            targets=(
                ActuatorTarget(
                    actuator_name=actuator_name,
                    normalized_position=target_position,
                ),
            ),
        )

    def _find_safe_recovery_time(self, request: PoseRequest, now_ns: int) -> int | None:
        delay_ns = 1
        for _ in range(64):
            candidate_ns = now_ns + delay_ns
            if self._motion_problem(request, candidate_ns) is None:
                return candidate_ns
            delay_ns *= 2
        return None

    def _recovery_result(
        self, *, accepted: bool, fault: SafetyFault | None
    ) -> TransitionResult:
        expose_authorization = self._recovery_wait is None
        return TransitionResult(
            accepted=accepted,
            state=self._state,
            fault=fault,
            recovery_request=(self._recovery_request if expose_authorization else None),
            recovery_authorization=(
                self._recovery_authorization if expose_authorization else None
            ),
            recovery_wait=self._recovery_wait,
        )

    def _commit_applied(self, request: PoseRequest, reported_ns: int) -> None:
        """Commit controller-confirmed command state, never inferred mechanics."""

        for target in request.targets:
            name = target.actuator_name
            elapsed_ns = reported_ns - self._last_applied_by_actuator[name]
            elapsed_seconds = elapsed_ns / 1_000_000_000 if elapsed_ns > 0 else None
            previous = self._committed_targets[name]
            self._committed_targets[name] = target.normalized_position
            self._committed_velocities[name] = (
                (target.normalized_position - previous) / elapsed_seconds
                if elapsed_seconds is not None
                else 0.0
            )
            self._last_applied_by_actuator[name] = reported_ns
        self._last_acknowledged_ns = reported_ns

    @staticmethod
    def _status_identity_matches(status: ActuatorStatus, request: PoseRequest) -> bool:
        return (
            status.request_id == request.request_id
            and status.run_id == request.run_id
            and status.hardware_id == request.hardware_id
            and status.calibration_sha256 == request.calibration_sha256
        )

    @staticmethod
    def _targets_match(
        applied: tuple[ActuatorTarget, ...], requested: tuple[ActuatorTarget, ...]
    ) -> bool:
        return {target.actuator_name: target for target in applied} == {
            target.actuator_name: target for target in requested
        }

    @staticmethod
    def _request_error_code(message: str) -> str:
        if "calibration_sha256" in message:
            return "calibration-mismatch"
        if "issued in the future" in message:
            return "request-issued-in-future"
        if "expired" in message:
            return "request-expired"
        if "hardware_id" in message:
            return "hardware-identity-mismatch"
        return "invalid-request"

    def _request_problem(
        self, request: PoseRequest, now_ns: int
    ) -> tuple[str, str] | None:
        try:
            self._manifest.validate_request(request, now_monotonic_ns=now_ns)
        except ValueError as error:
            return self._request_error_code(str(error)), str(error)
        if now_ns - request.issued_monotonic_ns >= self._limits.command_max_age_ns:
            return (
                "command-too-old",
                "request exceeds the supervisor command age limit",
            )
        return None

    def _enter_faulted(self, code: str, detail: str) -> TransitionResult:
        fault = self._record_fault(code, detail)
        self._state = RunState.FAULTED
        return TransitionResult(accepted=False, state=self._state, fault=fault)

    def _invalid_transition(
        self, operation: str, required: RunState
    ) -> TransitionResult:
        fault = self._make_fault(
            "invalid-transition",
            f"{operation} requires {required.value}; current state is "
            f"{self._state.value}",
        )
        return TransitionResult(accepted=False, state=self._state, fault=fault)

    def _record_fault(self, code: str, detail: str) -> SafetyFault:
        fault = self._make_fault(code, detail)
        self._fault = fault
        self._fault_history.append(fault)
        return fault

    def _make_fault(self, code: str, detail: str) -> SafetyFault:
        return SafetyFault(
            code=code, detail=detail, occurred_monotonic_ns=self._clock()
        )

    def _reset_disarmed(self) -> None:
        self._permit_registry.revoke_all()
        self._state = RunState.DISARMED
        self._fault = None
        self._preflight = None
        self._approval = None
        self._run_id = None
        self._pending_request = None
        self._recovery_request = None
        self._recovery_authorization = None
        self._recovery_wait = None
        self._originating_fault_code = None
        self._unknown_actuators = set()
        self._safe_state_verified = False
        self._last_applied_by_actuator = {}
        self._last_acknowledged_ns = None
        self._committed_targets = {
            actuator.name: 0.0 for actuator in self._manifest.actuators
        }
        self._committed_velocities = {
            actuator.name: 0.0 for actuator in self._manifest.actuators
        }
