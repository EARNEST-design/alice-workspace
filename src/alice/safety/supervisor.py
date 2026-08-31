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


class TransitionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    state: RunState
    fault: SafetyFault | None = None
    recovery_request: PoseRequest | None = None


class AuthorizationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorized: bool
    state: RunState
    request: PoseRequest | None = None
    fault: SafetyFault | None = None
    recovery_request: PoseRequest | None = None


class SafetySupervisor:
    """Final policy authority before an injected actuator adapter.

    Authorization is not application. Position, velocity, and watchdog history
    advance only after a matching ``APPLIED`` status is recorded.
    """

    def __init__(
        self,
        *,
        manifest: HardwareManifest,
        limits: SafetyLimits,
        clock: Callable[[], int],
    ) -> None:
        self._manifest = manifest
        self._limits = limits
        self._clock = clock
        self._state = RunState.DISARMED
        self._fault: SafetyFault | None = None
        self._fault_history: list[SafetyFault] = []
        self._preflight: PreflightEvidence | None = None
        self._approval: OperatorApproval | None = None
        self._run_id: str | None = None
        self._pending_request: PoseRequest | None = None
        self._recovery_request: PoseRequest | None = None
        self._recovery_counter = 0
        self._safe_state_verified = False
        self._committed_targets = {
            actuator.name: 0.0 for actuator in self._manifest.actuators
        }
        self._committed_velocities = {
            actuator.name: 0.0 for actuator in self._manifest.actuators
        }
        self._last_applied_ns: int | None = None

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
        return self._recovery_request

    @property
    def committed_targets(self) -> Mapping[str, float]:
        return MappingProxyType(self._committed_targets)

    @property
    def last_applied_monotonic_ns(self) -> int | None:
        return self._last_applied_ns

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
        self._last_applied_ns = now_ns
        self._committed_targets = {
            actuator.name: 0.0 for actuator in self._manifest.actuators
        }
        self._committed_velocities = {
            actuator.name: 0.0 for actuator in self._manifest.actuators
        }
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
        try:
            self._manifest.validate_request(request, now_monotonic_ns=now_ns)
        except ValueError as error:
            return self._abort_authorization(
                self._request_error_code(str(error)), str(error)
            )
        if now_ns - request.issued_monotonic_ns >= self._limits.command_max_age_ns:
            return self._abort_authorization(
                "command-too-old", "request exceeds the supervisor command age limit"
            )
        motion_problem = self._motion_problem(request, now_ns)
        if motion_problem is not None:
            return self._abort_authorization(*motion_problem)
        self._pending_request = request
        return AuthorizationDecision(
            authorized=True, state=self._state, request=request
        )

    def record_status(self, status: ActuatorStatus) -> TransitionResult:
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
            return self._begin_abort(
                "controller-error", status.fault_code or "actuator adapter failed"
            )
        if not self._targets_match(status.applied_targets, pending.targets):
            return self._begin_abort(
                "applied-target-mismatch",
                "applied targets differ from authorized targets",
            )
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
        assert self._last_applied_ns is not None
        if now_ns - self._last_applied_ns >= self._limits.watchdog_timeout_ns:
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

    def complete_abort(self) -> TransitionResult:
        if self._state is not RunState.ABORTING:
            return self._invalid_transition("complete_abort", RunState.ABORTING)
        fault = self._make_fault(
            "home-not-confirmed", "matching Home APPLIED status has not been recorded"
        )
        return TransitionResult(
            accepted=False,
            state=self._state,
            fault=fault,
            recovery_request=self._recovery_request,
        )

    def recovery_unavailable(self, detail: str) -> TransitionResult:
        if self._state is not RunState.ABORTING:
            return self._invalid_transition("recovery_unavailable", RunState.ABORTING)
        fault = self._record_fault("recovery-unavailable", detail)
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
        if not evidence.home_verified:
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
        assert self._last_applied_ns is not None
        elapsed_ns = now_ns - self._last_applied_ns
        if elapsed_ns <= 0:
            return "rate-limit-exceeded", "movement has no elapsed applied-state time"
        elapsed_seconds = elapsed_ns / 1_000_000_000
        for target in request.targets:
            name = target.actuator_name
            delta = target.normalized_position - self._committed_targets[name]
            if abs(delta) > self._limits.max_step:
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
        recovery = self._recovery_request
        if recovery is None or not self._status_identity_matches(status, recovery):
            fault = self._record_fault(
                "recovery-status-mismatch",
                "status does not match the supervisor-created Home request",
            )
            return TransitionResult(accepted=False, state=self._state, fault=fault)
        if recovery.is_expired(now_monotonic_ns=self._clock()):
            fault = self._record_fault(
                "recovery-expired",
                "Home request expired before its APPLIED status was recorded",
            )
            return TransitionResult(accepted=False, state=self._state, fault=fault)
        problem = self._status_problem(status, recovery)
        if problem is not None:
            fault = self._record_fault(*problem)
            return TransitionResult(accepted=False, state=self._state, fault=fault)
        if status.state is not ActuatorStatusState.APPLIED:
            fault = self._record_fault(
                "recovery-controller-error",
                status.fault_code or "Home request was not applied",
            )
            return TransitionResult(accepted=False, state=self._state, fault=fault)
        if not self._targets_match(status.applied_targets, recovery.targets):
            fault = self._record_fault(
                "recovery-target-mismatch",
                "Home status does not report all Home targets",
            )
            return TransitionResult(accepted=False, state=self._state, fault=fault)
        self._commit_applied(recovery, status.reported_monotonic_ns)
        self._pending_request = None
        self._recovery_request = None
        self._safe_state_verified = True
        self._state = RunState.FAULTED
        return TransitionResult(accepted=True, state=self._state, fault=self._fault)

    def _begin_abort(
        self,
        code: str,
        detail: str,
        *,
        communication_available: bool = True,
    ) -> TransitionResult:
        fault = self._record_fault(code, detail)
        self._state = RunState.ABORTING
        self._safe_state_verified = False
        self._recovery_request = (
            self._make_home_request() if communication_available else None
        )
        return TransitionResult(
            accepted=False,
            state=self._state,
            fault=fault,
            recovery_request=self._recovery_request,
        )

    def _abort_authorization(self, code: str, detail: str) -> AuthorizationDecision:
        result = self._begin_abort(code, detail)
        return AuthorizationDecision(
            authorized=False,
            state=result.state,
            fault=result.fault,
            recovery_request=result.recovery_request,
        )

    def _make_home_request(self) -> PoseRequest:
        assert self._run_id is not None
        now_ns = self._clock()
        self._recovery_counter += 1
        return PoseRequest(
            schema_version="pose-request/v1",
            request_id=f"safety-home-{self._recovery_counter}-{now_ns}",
            run_id=self._run_id,
            hardware_id=self._manifest.hardware_id,
            calibration_sha256=self._manifest.calibration_sha256,
            issued_monotonic_ns=now_ns,
            expires_monotonic_ns=now_ns + self._limits.recovery_command_ttl_ns,
            targets=tuple(
                ActuatorTarget(actuator_name=actuator.name, normalized_position=0.0)
                for actuator in self._manifest.actuators
            ),
        )

    def _commit_applied(self, request: PoseRequest, reported_ns: int) -> None:
        assert self._last_applied_ns is not None
        elapsed_ns = reported_ns - self._last_applied_ns
        elapsed_seconds = elapsed_ns / 1_000_000_000 if elapsed_ns > 0 else None
        for target in request.targets:
            name = target.actuator_name
            previous = self._committed_targets[name]
            self._committed_targets[name] = target.normalized_position
            self._committed_velocities[name] = (
                (target.normalized_position - previous) / elapsed_seconds
                if elapsed_seconds is not None
                else 0.0
            )
        self._last_applied_ns = reported_ns

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
        self._state = RunState.DISARMED
        self._fault = None
        self._preflight = None
        self._approval = None
        self._run_id = None
        self._pending_request = None
        self._recovery_request = None
        self._safe_state_verified = False
        self._last_applied_ns = None
        self._committed_targets = {
            actuator.name: 0.0 for actuator in self._manifest.actuators
        }
        self._committed_velocities = {
            actuator.name: 0.0 for actuator in self._manifest.actuators
        }
