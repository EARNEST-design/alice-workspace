"""Deterministic, fail-closed supervision of semantic actuator requests.

This module deliberately has no device, serial, ROS, camera, or perception
dependencies.  It decides whether a request may cross the actuator boundary;
an adapter remains responsible for applying an authorized request.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from alice.contracts.actuation import ActuatorStatus, ActuatorStatusState, PoseRequest
from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.hardware.manifest import HardwareManifest


class RunState(StrEnum):
    DISARMED = "disarmed"
    PREFLIGHT = "preflight"
    ARMED = "armed"
    RUNNING = "running"
    ABORTING = "aborting"
    FAULTED = "faulted"


class SafetyLimits(BaseModel):
    """Run-specific limits reviewed before constructing the supervisor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_step: Annotated[float, Field(gt=0.0, le=2.0, allow_inf_nan=False)]
    max_rate_per_second: Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
    watchdog_timeout_ns: Annotated[int, Field(gt=0)]
    approval_max_age_ns: Annotated[int, Field(gt=0)]


class PreflightEvidence(BaseModel):
    """Contemporaneous facts gathered by a hardware bring-up procedure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: NonEmptyString
    hardware_id: NonEmptyString
    calibration_sha256: Sha256Hex
    controller_serial: NonEmptyString
    requirement_results: Mapping[NonEmptyString, bool]
    competing_process_detected: bool
    controller_error_codes: tuple[Annotated[int, Field(ge=0)], ...]
    home_verified: bool


class OperatorApproval(BaseModel):
    """Explicit approval captured immediately before arming one run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: NonEmptyString
    run_id: NonEmptyString
    confirmed_monotonic_ns: Annotated[int, Field(ge=0)]


class SafetyFault(BaseModel):
    """Structured evidence explaining a fail-closed transition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: NonEmptyString
    detail: NonEmptyString
    occurred_monotonic_ns: Annotated[int, Field(ge=0)]


class TransitionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    state: RunState
    fault: SafetyFault | None = None


class AuthorizationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorized: bool
    state: RunState
    request: PoseRequest | None = None
    fault: SafetyFault | None = None


class SafetySupervisor:
    """Stateful final authority before any actuator adapter.

    The injected monotonic clock makes all freshness, approval, rate, and
    watchdog decisions replayable.  Discovery or construction cannot arm it.
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
        self._preflight: PreflightEvidence | None = None
        self._run_id: str | None = None
        self._last_authorized_ns: int | None = None
        self._last_targets = {
            actuator.name: 0.0 for actuator in self._manifest.actuators
        }
        self._pending_request: PoseRequest | None = None

    @property
    def state(self) -> RunState:
        return self._state

    @property
    def fault(self) -> SafetyFault | None:
        return self._fault

    def preflight(self, evidence: PreflightEvidence) -> TransitionResult:
        if self._state is not RunState.DISARMED:
            raise RuntimeError("preflight requires DISARMED state")
        problem = self._preflight_problem(evidence)
        if problem is not None:
            return self._enter_faulted(*problem)
        self._preflight = evidence
        self._run_id = evidence.run_id
        self._state = RunState.PREFLIGHT
        return TransitionResult(accepted=True, state=self._state)

    def arm(self, approval: OperatorApproval | None) -> TransitionResult:
        if self._state is not RunState.PREFLIGHT:
            raise RuntimeError("arm requires PREFLIGHT state")
        now_ns = self._clock()
        if approval is None:
            return self._enter_faulted(
                "approval-missing", "operator approval is required"
            )
        if approval.run_id != self._run_id:
            return self._enter_faulted(
                "approval-run-mismatch", "approval is for a different run"
            )
        if approval.confirmed_monotonic_ns > now_ns:
            return self._enter_faulted(
                "approval-issued-in-future", "approval timestamp is in the future"
            )
        if now_ns - approval.confirmed_monotonic_ns >= self._limits.approval_max_age_ns:
            return self._enter_faulted("approval-stale", "operator approval is stale")
        assert self._preflight is not None
        problem = self._preflight_problem(self._preflight)
        if problem is not None:
            return self._enter_faulted(*problem)
        self._state = RunState.ARMED
        return TransitionResult(accepted=True, state=self._state)

    def authorize(self, request: PoseRequest) -> AuthorizationDecision:
        if self._state not in (RunState.ARMED, RunState.RUNNING):
            return self._deny_without_transition(
                "invalid-run-state", f"requests are not accepted in {self._state.value}"
            )
        now_ns = self._clock()
        if request.run_id != self._run_id:
            return self._abort_authorization(
                "run-identity-mismatch", "request is for a different run"
            )
        try:
            self._manifest.validate_request(request, now_monotonic_ns=now_ns)
        except ValueError as error:
            message = str(error)
            if "calibration_sha256" in message:
                code = "calibration-mismatch"
            elif "issued in the future" in message:
                code = "request-issued-in-future"
            elif "expired" in message:
                code = "request-expired"
            elif "hardware_id" in message:
                code = "hardware-identity-mismatch"
            else:
                code = "invalid-request"
            return self._abort_authorization(code, message)

        proposed = dict(self._last_targets)
        for target in request.targets:
            previous = self._last_targets[target.actuator_name]
            delta = abs(target.normalized_position - previous)
            if delta > self._limits.max_step:
                return self._abort_authorization(
                    "step-limit-exceeded",
                    f"{target.actuator_name} step {delta} exceeds "
                    f"{self._limits.max_step}",
                )
            if self._last_authorized_ns is not None:
                elapsed_ns = now_ns - self._last_authorized_ns
                if elapsed_ns <= 0:
                    if delta > 0.0:
                        return self._abort_authorization(
                            "rate-limit-exceeded",
                            "non-zero movement has no elapsed time",
                        )
                else:
                    rate = delta / (elapsed_ns / 1_000_000_000)
                    if rate > self._limits.max_rate_per_second:
                        return self._abort_authorization(
                            "rate-limit-exceeded",
                            f"{target.actuator_name} rate {rate} exceeds "
                            f"{self._limits.max_rate_per_second}",
                        )
            proposed[target.actuator_name] = target.normalized_position

        self._last_targets = proposed
        self._last_authorized_ns = now_ns
        self._pending_request = request
        self._state = RunState.RUNNING
        return AuthorizationDecision(
            authorized=True, state=self._state, request=request
        )

    def record_status(self, status: ActuatorStatus) -> TransitionResult:
        if self._state is not RunState.RUNNING or self._pending_request is None:
            return self._transition_fault(
                "unexpected-status", "actuator status arrived without a pending request"
            )
        pending = self._pending_request
        identity_matches = (
            status.request_id == pending.request_id
            and status.run_id == pending.run_id
            and status.hardware_id == pending.hardware_id
            and status.calibration_sha256 == pending.calibration_sha256
        )
        if not identity_matches:
            return self._transition_fault(
                "status-identity-mismatch",
                "actuator status does not match pending request",
            )
        now_ns = self._clock()
        if status.reported_monotonic_ns > now_ns:
            return self._transition_fault(
                "status-issued-in-future", "actuator status timestamp is in the future"
            )
        if status.reported_monotonic_ns < pending.issued_monotonic_ns:
            return self._transition_fault(
                "status-before-request",
                "actuator status predates its pending request",
            )
        if now_ns - status.reported_monotonic_ns >= self._limits.watchdog_timeout_ns:
            return self._transition_fault(
                "status-stale", "actuator status exceeded the watchdog age limit"
            )
        if status.state is not ActuatorStatusState.APPLIED:
            return self._transition_fault(
                "controller-error", status.fault_code or "actuator adapter failed"
            )
        requested = {target.actuator_name: target for target in pending.targets}
        applied = {target.actuator_name: target for target in status.applied_targets}
        if applied != requested:
            return self._transition_fault(
                "applied-target-mismatch",
                "applied targets differ from authorized targets",
            )
        self._pending_request = None
        return TransitionResult(accepted=True, state=self._state)

    def watchdog(self, now_ns: int) -> TransitionResult:
        if now_ns < 0:
            raise ValueError("watchdog timestamp must be non-negative")
        clock_now_ns = self._clock()
        if now_ns != clock_now_ns:
            return self._transition_fault(
                "watchdog-time-mismatch",
                "watchdog timestamp does not match the injected monotonic clock",
            )
        if self._state is not RunState.RUNNING or self._last_authorized_ns is None:
            return TransitionResult(accepted=True, state=self._state)
        if now_ns - self._last_authorized_ns >= self._limits.watchdog_timeout_ns:
            return self._transition_fault(
                "watchdog-expired",
                "no authorized request arrived before watchdog deadline",
            )
        return TransitionResult(accepted=True, state=self._state)

    def complete_abort(self) -> TransitionResult:
        if self._state is not RunState.ABORTING:
            raise RuntimeError("complete_abort requires ABORTING state")
        self._state = RunState.FAULTED
        return TransitionResult(accepted=True, state=self._state, fault=self._fault)

    def acknowledge_fault(self, evidence: PreflightEvidence) -> TransitionResult:
        if self._state is not RunState.FAULTED:
            raise RuntimeError("fault acknowledgement requires FAULTED state")
        problem = self._preflight_problem(evidence)
        if problem is not None:
            return TransitionResult(
                accepted=False, state=self._state, fault=self._fault
            )
        self._state = RunState.DISARMED
        self._fault = None
        self._preflight = None
        self._run_id = None
        self._last_authorized_ns = None
        self._pending_request = None
        self._last_targets = {
            actuator.name: 0.0 for actuator in self._manifest.actuators
        }
        return TransitionResult(accepted=True, state=self._state)

    def _preflight_problem(self, evidence: PreflightEvidence) -> tuple[str, str] | None:
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
        missing = required - set(evidence.requirement_results)
        failed = {
            name
            for name in required
            if evidence.requirement_results.get(name) is not True
        }
        if missing or failed:
            names = sorted(missing | failed)
            return "preflight-requirement-failed", f"unmet requirements: {names}"
        if evidence.competing_process_detected:
            return "competing-process", "a competing actuator process was detected"
        if evidence.controller_error_codes:
            return "controller-error", "controller error register is non-zero"
        if not evidence.home_verified:
            return "home-not-verified", "reviewed Home positions were not verified"
        return None

    def _enter_faulted(self, code: str, detail: str) -> TransitionResult:
        self._fault = self._make_fault(code, detail)
        self._state = RunState.FAULTED
        return TransitionResult(accepted=False, state=self._state, fault=self._fault)

    def _transition_fault(self, code: str, detail: str) -> TransitionResult:
        self._fault = self._make_fault(code, detail)
        self._state = (
            RunState.ABORTING
            if self._state in (RunState.ARMED, RunState.RUNNING)
            else RunState.FAULTED
        )
        return TransitionResult(accepted=False, state=self._state, fault=self._fault)

    def _abort_authorization(self, code: str, detail: str) -> AuthorizationDecision:
        result = self._transition_fault(code, detail)
        return AuthorizationDecision(
            authorized=False, state=result.state, fault=result.fault
        )

    def _deny_without_transition(self, code: str, detail: str) -> AuthorizationDecision:
        fault = self._make_fault(code, detail)
        return AuthorizationDecision(authorized=False, state=self._state, fault=fault)

    def _make_fault(self, code: str, detail: str) -> SafetyFault:
        return SafetyFault(
            code=code, detail=detail, occurred_monotonic_ns=self._clock()
        )
