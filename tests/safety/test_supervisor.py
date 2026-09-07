"""Transition-table tests for the hardware-independent safety supervisor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from alice.contracts.actuation import ActuatorStatus, ActuatorStatusState, PoseRequest
from alice.hardware.manifest import HardwareManifest, load_manifest
from alice.safety.supervisor import (
    AbortReason,
    OperatorApproval,
    PreflightEvidence,
    RunState,
    SafetyLimits,
    SafetySupervisor,
)

MANIFEST_PATH = Path(__file__).parents[2] / "hardware" / "alice-face-v1.yaml"


@dataclass
class FakeClock:
    now_ns: int = 1_000_000_000

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, nanoseconds: int) -> None:
        self.now_ns += nanoseconds


@pytest.fixture
def manifest() -> HardwareManifest:
    return load_manifest(MANIFEST_PATH)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def passing_preflight(
    manifest: HardwareManifest, *, run_id: str = "run-001"
) -> PreflightEvidence:
    return PreflightEvidence(
        run_id=run_id,
        hardware_id=manifest.hardware_id,
        calibration_sha256=manifest.calibration_sha256,
        controller_serial=manifest.controller.serial_number,
        requirement_results={
            requirement.requirement_id: True
            for requirement in manifest.preflight_requirements
        },
        competing_process_detected=False,
        controller_error_codes=(),
        home_verified=True,
        observed_monotonic_ns=1_000_000_000,
    )


def make_supervisor(manifest: HardwareManifest, clock: FakeClock) -> SafetySupervisor:
    return SafetySupervisor(
        manifest=manifest,
        limits=SafetyLimits(
            max_step=0.20,
            max_rate_per_second=0.50,
            watchdog_timeout_ns=500_000_000,
            approval_max_age_ns=10_000_000_000,
            preflight_max_age_ns=20_000_000_000,
            command_max_age_ns=100_000_000,
            max_acceleration_per_second_squared=2.0,
            recovery_command_ttl_ns=100_000_000,
        ),
        clock=clock,
    )


def approval(clock: FakeClock, *, run_id: str = "run-001") -> OperatorApproval:
    return OperatorApproval(
        approval_id="operator-approved-run-001",
        run_id=run_id,
        confirmed_monotonic_ns=clock.now_ns,
    )


def request(
    manifest: HardwareManifest,
    clock: FakeClock,
    *,
    position: float = 0.10,
    run_id: str = "run-001",
    calibration_sha256: str | None = None,
    issued_monotonic_ns: int | None = None,
    expires_monotonic_ns: int | None = None,
    actuator_name: str = "mouth_open",
) -> PoseRequest:
    issued = clock.now_ns if issued_monotonic_ns is None else issued_monotonic_ns
    expires = (
        issued + 100_000_000 if expires_monotonic_ns is None else expires_monotonic_ns
    )
    return PoseRequest(
        schema_version="pose-request/v1",
        request_id=f"request-{issued}-{position}",
        run_id=run_id,
        hardware_id=manifest.hardware_id,
        calibration_sha256=calibration_sha256 or manifest.calibration_sha256,
        issued_monotonic_ns=issued,
        expires_monotonic_ns=expires,
        targets=({"actuator_name": actuator_name, "normalized_position": position},),
    )


def applied_status(
    accepted: PoseRequest,
    clock: FakeClock,
    *,
    targets: tuple[object, ...] | None = None,
) -> ActuatorStatus:
    return ActuatorStatus.model_validate(
        {
            "schema_version": "actuator-status/v1",
            "request_id": accepted.request_id,
            "run_id": accepted.run_id,
            "hardware_id": accepted.hardware_id,
            "calibration_sha256": accepted.calibration_sha256,
            "reported_monotonic_ns": clock.now_ns,
            "state": ActuatorStatusState.APPLIED,
            "applied_targets": targets or accepted.targets,
        }
    )


def apply_request(
    supervisor: SafetySupervisor,
    manifest: HardwareManifest,
    clock: FakeClock,
    *,
    position: float,
    actuator_name: str = "mouth_open",
) -> PoseRequest:
    accepted = request(
        manifest,
        clock,
        position=position,
        actuator_name=actuator_name,
    )
    assert supervisor.authorize(accepted).authorized
    assert supervisor.record_status(applied_status(accepted, clock)).accepted
    return accepted


def recovery_after_wait(supervisor: SafetySupervisor, clock: FakeClock) -> PoseRequest:
    wait = supervisor.recovery_wait
    assert wait is not None
    clock.advance(wait.retry_not_before_monotonic_ns - clock.now_ns)
    result = supervisor.retry_recovery(clock.now_ns)
    assert result.accepted is False
    assert result.recovery_request is not None
    return result.recovery_request


def arm(
    supervisor: SafetySupervisor, manifest: HardwareManifest, clock: FakeClock
) -> None:
    assert supervisor.preflight(passing_preflight(manifest)).accepted
    assert supervisor.state is RunState.PREFLIGHT
    assert supervisor.arm(approval(clock)).accepted
    assert supervisor.state is RunState.ARMED


def start(
    supervisor: SafetySupervisor, manifest: HardwareManifest, clock: FakeClock
) -> None:
    arm(supervisor, manifest, clock)
    assert supervisor.start().accepted
    assert supervisor.state is RunState.RUNNING
    clock.advance(250_000_000)


def faulted(supervisor: SafetySupervisor) -> None:
    assert supervisor.state is RunState.ABORTING
    result = supervisor.recovery_unavailable("test transport is unavailable")
    assert result.accepted is True
    assert supervisor.state is RunState.FAULTED


def test_happy_path_follows_disarmed_preflight_armed_running(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)

    arm(supervisor, manifest, clock)
    assert supervisor.start().accepted
    assert supervisor.state is RunState.RUNNING
    clock.advance(250_000_000)
    decision = supervisor.authorize(request(manifest, clock))

    assert decision.authorized is True
    assert decision.request is not None
    assert decision.fault is None
    assert supervisor.state is RunState.RUNNING


def test_authorize_requires_explicit_start(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    """Implicitly starting on a command bypasses the reviewed start gate."""

    supervisor = make_supervisor(manifest, clock)
    arm(supervisor, manifest, clock)

    decision = supervisor.authorize(request(manifest, clock))

    assert decision.authorized is False
    assert decision.fault is not None
    assert decision.fault.code == "invalid-transition"
    assert supervisor.state is RunState.ARMED
    assert supervisor.pending_request is None


def test_complete_run_requires_confirmed_controller_home_and_leaves_running(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    clock.advance(500_000_000)
    apply_request(supervisor, manifest, clock, position=0.10)

    rejected = supervisor.complete_run()

    assert rejected.accepted is False
    assert rejected.state is RunState.ABORTING
    assert rejected.fault is not None
    assert rejected.fault.code == "complete-not-home"


def test_complete_run_at_controller_home_is_terminal_without_clearing_history(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)

    completed = supervisor.complete_run()

    assert completed.accepted is True
    assert completed.state is RunState.DISARMED
    assert supervisor.state is RunState.DISARMED
    assert supervisor.pending_request is None


def test_cancel_armed_revokes_approval_and_returns_disarmed_without_motion(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    arm(supervisor, manifest, clock)

    cancelled = supervisor.cancel_armed("pre-run composition rejected")

    assert cancelled.accepted is True
    assert cancelled.state is RunState.DISARMED
    assert supervisor.state is RunState.DISARMED
    assert supervisor.operator_approval is None
    assert supervisor.pending_request is None
    assert all(value == 0.0 for value in supervisor.committed_targets.values())


def test_only_one_request_can_be_in_flight_and_unapplied_target_is_not_committed(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    """Queuing over an unacknowledged command loses physical-state provenance."""

    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    first = request(manifest, clock, position=0.10)
    assert supervisor.authorize(first).authorized

    second = supervisor.authorize(request(manifest, clock, position=0.11))

    assert second.authorized is False
    assert second.fault is not None
    assert second.fault.code == "request-in-flight"
    assert second.recovery_request is None
    assert second.recovery_wait is not None
    assert second.recovery_wait.reason == "position-reconciliation-required"
    assert supervisor.committed_targets["mouth_open"] == 0.0
    assert supervisor.last_applied_monotonic_ns == 1_000_000_000
    assert supervisor.state is RunState.ABORTING


def test_matching_applied_status_is_the_only_commit_point(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    """Authorization alone must not advance position or velocity history."""

    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    clock.advance(250_000_000)
    accepted = request(manifest, clock, position=0.10)
    assert supervisor.authorize(accepted).authorized
    assert supervisor.committed_targets["mouth_open"] == 0.0

    result = supervisor.record_status(applied_status(accepted, clock))

    assert result.accepted is True
    assert supervisor.committed_targets["mouth_open"] == 0.10
    assert supervisor.last_applied_monotonic_ns == clock.now_ns


def test_unacknowledged_command_does_not_refresh_watchdog(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    """Authorization traffic cannot stand in for actuator acknowledgements."""

    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    clock.advance(400_000_000)
    assert supervisor.authorize(request(manifest, clock, position=0.10)).authorized
    clock.advance(100_000_000)

    result = supervisor.watchdog(clock.now_ns)

    assert result.accepted is False
    assert result.fault is not None
    assert result.fault.code == "watchdog-expired"
    assert result.recovery_request is None
    assert result.recovery_wait is not None
    assert result.recovery_wait.reason == "position-reconciliation-required"
    assert supervisor.state is RunState.ABORTING


def test_missing_approval_fails_closed(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    supervisor.preflight(passing_preflight(manifest))

    result = supervisor.arm(None)

    assert result.accepted is False
    assert result.fault is not None
    assert result.fault.code == "approval-missing"
    assert supervisor.state is RunState.FAULTED


@pytest.mark.parametrize(
    ("mutation", "fault_code"),
    [
        ("linkage", "preflight-requirement-failed"),
        ("general", "preflight-requirement-failed"),
        ("competing", "competing-process"),
        ("controller", "controller-error"),
        ("home", "home-not-verified"),
    ],
)
def test_unmet_preflight_conditions_enter_faulted(
    manifest: HardwareManifest,
    clock: FakeClock,
    mutation: str,
    fault_code: str,
) -> None:
    evidence = passing_preflight(manifest)
    values = evidence.model_dump()
    if mutation == "linkage":
        values["requirement_results"]["channel-10-linkage-inspection"] = False
    elif mutation == "general":
        values["requirement_results"]["mechanical-clearance-verified"] = False
    elif mutation == "competing":
        values["competing_process_detected"] = True
    elif mutation == "controller":
        values["controller_error_codes"] = (4,)
    else:
        values["home_verified"] = False

    supervisor = make_supervisor(manifest, clock)
    result = supervisor.preflight(PreflightEvidence.model_validate(values))

    assert result.accepted is False
    assert result.fault is not None
    assert result.fault.code == fault_code
    assert supervisor.state is RunState.FAULTED


@pytest.mark.parametrize(
    ("request_overrides", "fault_code"),
    [
        (
            {"issued_monotonic_ns": 900_000_000, "expires_monotonic_ns": 1_000_000_000},
            "request-expired",
        ),
        ({"issued_monotonic_ns": 2_000_000_000}, "request-issued-in-future"),
        ({"calibration_sha256": "b" * 64}, "calibration-mismatch"),
        ({"run_id": "different-run"}, "run-identity-mismatch"),
        ({"position": 0.21}, "step-limit-exceeded"),
    ],
)
def test_invalid_request_aborts_without_forwarding(
    manifest: HardwareManifest,
    clock: FakeClock,
    request_overrides: dict[str, object],
    fault_code: str,
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)

    decision = supervisor.authorize(request(manifest, clock, **request_overrides))  # type: ignore[arg-type]

    assert decision.authorized is False
    assert decision.request is None
    assert decision.fault is not None
    assert decision.fault.code == fault_code
    assert supervisor.state is RunState.ABORTING


def test_excessive_rate_aborts_second_request(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    clock.advance(500_000_000)
    first = request(manifest, clock, position=0.10)
    assert supervisor.authorize(first).authorized
    assert supervisor.record_status(applied_status(first, clock)).accepted
    clock.advance(100_000_000)

    decision = supervisor.authorize(request(manifest, clock, position=0.16))

    assert decision.authorized is False
    assert decision.fault is not None
    assert decision.fault.code == "rate-limit-exceeded"
    assert supervisor.state is RunState.ABORTING


def test_controller_fault_status_aborts_and_preserves_fault(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    accepted = request(manifest, clock)
    assert supervisor.authorize(accepted).authorized
    status = ActuatorStatus(
        schema_version="actuator-status/v1",
        request_id=accepted.request_id,
        run_id=accepted.run_id,
        hardware_id=accepted.hardware_id,
        calibration_sha256=accepted.calibration_sha256,
        reported_monotonic_ns=clock.now_ns,
        state=ActuatorStatusState.FAULT,
        fault_code="controller-error-register",
    )

    result = supervisor.record_status(status)

    assert result.accepted is False
    assert result.fault is not None
    assert result.fault.code == "position-uncertain"
    assert result.recovery_request is None
    assert result.recovery_wait is not None
    assert result.recovery_wait.reason == "operator-intervention-required"
    assert [fault.code for fault in supervisor.fault_history[-2:]] == [
        "controller-error",
        "position-uncertain",
    ]
    assert supervisor.state is RunState.ABORTING


def test_watchdog_expiry_aborts_at_deadline(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    assert supervisor.authorize(request(manifest, clock)).authorized
    clock.advance(500_000_000)

    result = supervisor.watchdog(clock.now_ns)

    assert result.accepted is False
    assert result.fault is not None
    assert result.fault.code == "watchdog-expired"
    assert supervisor.state is RunState.ABORTING


def test_stale_watchdog_argument_cannot_suppress_expiry(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    """Trusting caller-supplied old time would permit watchdog bypass."""

    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    heartbeat_ns = clock.now_ns
    assert supervisor.authorize(request(manifest, clock)).authorized
    clock.advance(500_000_000)

    result = supervisor.watchdog(heartbeat_ns)

    assert result.accepted is False
    assert result.fault is not None
    assert result.fault.code == "watchdog-time-mismatch"
    assert supervisor.state is RunState.ABORTING


def test_stale_applied_status_aborts_instead_of_confirming_motion(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    """Accepting a delayed status would misrepresent current physical state."""

    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    accepted = request(manifest, clock)
    assert supervisor.authorize(accepted).authorized
    clock.advance(500_000_000)
    status = ActuatorStatus(
        schema_version="actuator-status/v1",
        request_id=accepted.request_id,
        run_id=accepted.run_id,
        hardware_id=accepted.hardware_id,
        calibration_sha256=accepted.calibration_sha256,
        reported_monotonic_ns=clock.now_ns - 500_000_000,
        state=ActuatorStatusState.APPLIED,
        applied_targets=accepted.targets,
    )

    result = supervisor.record_status(status)

    assert result.accepted is False
    assert result.fault is not None
    assert result.fault.code == "status-stale"
    assert supervisor.state is RunState.ABORTING


@pytest.mark.parametrize(
    "reason", [AbortReason.OPERATOR_REQUEST, AbortReason.CAMERA_LOSS]
)
def test_explicit_runtime_abort_creates_trusted_single_actuator_recovery_request(
    manifest: HardwareManifest, clock: FakeClock, reason: AbortReason
) -> None:
    """An abort must yield a bounded semantic Home command, never an adapter call."""

    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    apply_request(supervisor, manifest, clock, position=0.10)
    clock.advance(500_000_000)

    result = supervisor.abort(reason)

    assert result.accepted is False
    assert result.fault is not None
    assert result.fault.code == reason.value
    assert result.recovery_request is not None
    home = result.recovery_request
    assert home.run_id == "run-001"
    assert home.hardware_id == manifest.hardware_id
    assert home.calibration_sha256 == manifest.calibration_sha256
    assert home.issued_monotonic_ns == clock.now_ns
    assert home.expires_monotonic_ns == clock.now_ns + 100_000_000
    assert len(home.targets) == 1
    assert home.targets[0].actuator_name == "mouth_open"
    assert home.targets[0].normalized_position == 0.0
    assert result.recovery_authorization is not None
    assert result.recovery_authorization.request == home
    assert result.recovery_authorization.originating_fault_code == reason.value
    assert supervisor.state is RunState.ABORTING
    assert supervisor.safe_state_verified is False


def test_matching_home_applied_status_is_required_before_faulted_safe_state(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    """A generated Home request is not evidence that Home was physically reached."""

    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    apply_request(supervisor, manifest, clock, position=0.10)
    clock.advance(500_000_000)
    recovery = supervisor.abort(AbortReason.OPERATOR_REQUEST).recovery_request
    assert recovery is not None

    premature = supervisor.complete_abort()
    assert premature.accepted is False
    assert premature.fault is not None
    assert premature.fault.code == "home-not-confirmed"
    assert supervisor.state is RunState.ABORTING
    assert supervisor.safe_state_verified is False

    confirmed = supervisor.record_status(applied_status(recovery, clock))
    assert confirmed.accepted is True
    assert supervisor.state is RunState.FAULTED
    assert supervisor.safe_state_verified is True
    assert all(value == 0.0 for value in supervisor.committed_targets.values())


def test_mismatched_home_status_does_not_claim_safe_state(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    apply_request(supervisor, manifest, clock, position=0.10)
    clock.advance(500_000_000)
    recovery = supervisor.abort(AbortReason.CAMERA_LOSS).recovery_request
    assert recovery is not None
    wrong = recovery.model_copy(update={"request_id": "wrong-home-request"})

    result = supervisor.record_status(applied_status(wrong, clock))

    assert result.accepted is False
    assert supervisor.state is RunState.ABORTING
    assert supervisor.safe_state_verified is False
    assert supervisor.fault_history[-1].code == "recovery-status-mismatch"


def test_expired_home_status_does_not_claim_safe_state(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    apply_request(supervisor, manifest, clock, position=0.10)
    clock.advance(500_000_000)
    recovery = supervisor.abort(AbortReason.CAMERA_LOSS).recovery_request
    assert recovery is not None
    clock.advance(100_000_000)

    result = supervisor.record_status(applied_status(recovery, clock))

    assert result.accepted is False
    assert result.fault is not None
    assert result.fault.code == "recovery-expired"
    assert supervisor.state is RunState.ABORTING
    assert supervisor.safe_state_verified is False


def test_expired_unacknowledged_recovery_is_not_reissued_from_stale_position(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    apply_request(supervisor, manifest, clock, position=0.10)
    clock.advance(500_000_000)
    recovery = supervisor.abort(AbortReason.CAMERA_LOSS).recovery_request
    assert recovery is not None
    clock.advance(100_000_000)

    result = supervisor.retry_recovery(clock.now_ns)

    assert result.accepted is False
    assert result.recovery_request is None
    assert result.recovery_authorization is None
    assert result.recovery_wait is not None
    assert result.recovery_wait.reason == "position-reconciliation-required"
    assert supervisor.safe_state_verified is False


def test_faulted_recovery_without_position_evidence_requires_intervention(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    apply_request(supervisor, manifest, clock, position=0.10)
    clock.advance(500_000_000)
    recovery = supervisor.abort(AbortReason.CAMERA_LOSS).recovery_request
    assert recovery is not None
    failed = ActuatorStatus(
        schema_version="actuator-status/v1",
        request_id=recovery.request_id,
        run_id=recovery.run_id,
        hardware_id=recovery.hardware_id,
        calibration_sha256=recovery.calibration_sha256,
        reported_monotonic_ns=clock.now_ns,
        state=ActuatorStatusState.FAULT,
        fault_code="transport-lost-after-write",
    )

    result = supervisor.record_status(failed)

    assert result.accepted is False
    assert result.recovery_request is None
    assert result.recovery_wait is not None
    assert result.recovery_wait.reason == "operator-intervention-required"
    retry = supervisor.retry_recovery(clock.now_ns)
    assert retry.recovery_request is None


def test_unsolicited_runtime_status_aborts_with_home_recovery(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    """Ignoring an unexpected controller status leaves state ambiguous."""

    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    unrequested = request(manifest, clock)

    result = supervisor.record_status(applied_status(unrequested, clock))

    assert result.accepted is False
    assert result.fault is not None
    assert result.fault.code == "unexpected-status"
    assert result.recovery_request is not None
    assert supervisor.state is RunState.ABORTING


def test_recovery_unavailable_preserves_original_and_transport_faults(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    supervisor.abort(AbortReason.CAMERA_LOSS, communication_available=False)

    result = supervisor.recovery_unavailable("controller disconnected")

    assert result.accepted is True
    assert supervisor.state is RunState.FAULTED
    assert supervisor.safe_state_verified is False
    assert [fault.code for fault in supervisor.fault_history[-2:]] == [
        "camera-loss",
        "recovery-unavailable",
    ]


def test_old_request_is_rejected_even_with_long_caller_expiry(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    old = request(
        manifest,
        clock,
        issued_monotonic_ns=clock.now_ns - 100_000_000,
        expires_monotonic_ns=clock.now_ns + 10_000_000_000,
    )

    result = supervisor.authorize(old)

    assert result.authorized is False
    assert result.fault is not None
    assert result.fault.code == "command-too-old"
    assert result.recovery_request is not None


def test_acceleration_uses_only_committed_applied_history(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    clock.advance(500_000_000)
    first = request(manifest, clock, position=0.10)
    assert supervisor.authorize(first).authorized
    assert supervisor.record_status(applied_status(first, clock)).accepted
    clock.advance(100_000_000)

    accelerated = supervisor.authorize(request(manifest, clock, position=0.15))

    assert accelerated.authorized is False
    assert accelerated.fault is not None
    assert accelerated.fault.code == "acceleration-limit-exceeded"
    assert supervisor.committed_targets["mouth_open"] == 0.10


@pytest.mark.parametrize("endpoint", [-1.0, 1.0])
def test_recovery_from_endpoint_is_single_actuator_bounded_and_multi_step(
    manifest: HardwareManifest, clock: FakeClock, endpoint: float
) -> None:
    """Recovery must not turn a safe forward trajectory into one unsafe jump."""

    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    direction = 1.0 if endpoint > 0 else -1.0
    for index in range(1, 11):
        apply_request(
            supervisor,
            manifest,
            clock,
            position=direction * index / 10,
        )
        clock.advance(250_000_000)
    assert supervisor.committed_targets["mouth_open"] == endpoint

    result = supervisor.abort(AbortReason.OPERATOR_REQUEST)
    previous = endpoint
    previous_applied_ns = 3_500_000_000
    previous_velocity = direction * 0.4
    recovery_targets: list[float] = []
    while supervisor.state is RunState.ABORTING:
        if result.recovery_wait is not None:
            assert result.recovery_wait.retry_not_before_monotonic_ns > clock.now_ns
            clock.advance(
                result.recovery_wait.retry_not_before_monotonic_ns - clock.now_ns
            )
            result = supervisor.retry_recovery(clock.now_ns)
            continue
        recovery = result.recovery_request
        assert recovery is not None
        assert result.recovery_authorization is not None
        assert result.recovery_authorization.request == recovery
        assert len(recovery.targets) == 1
        target = recovery.targets[0]
        assert target.actuator_name == "mouth_open"
        assert abs(target.normalized_position - previous) <= 0.20 + 1e-12
        assert abs(target.normalized_position) < abs(previous)
        manifest.validate_request(
            recovery, now_monotonic_ns=recovery.issued_monotonic_ns
        )
        elapsed_seconds = (
            recovery.issued_monotonic_ns - previous_applied_ns
        ) / 1_000_000_000
        velocity = (target.normalized_position - previous) / elapsed_seconds
        acceleration = abs(velocity - previous_velocity) / elapsed_seconds
        assert abs(velocity) <= 0.50 + 1e-12
        assert acceleration <= 2.0 + 1e-12
        recovery_targets.append(target.normalized_position)
        previous = target.normalized_position
        previous_applied_ns = recovery.issued_monotonic_ns
        previous_velocity = velocity
        result = supervisor.record_status(applied_status(recovery, clock))

    assert len(recovery_targets) == 5
    assert recovery_targets[-1] == 0.0
    assert supervisor.state is RunState.FAULTED
    assert supervisor.safe_state_verified is True
    assert supervisor.committed_targets["mouth_open"] == 0.0


def test_alternating_actuators_use_independent_position_timestamps(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    """Movement on actuator A must not shorten actuator B's elapsed interval."""

    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    apply_request(supervisor, manifest, clock, position=0.10)
    clock.advance(250_000_000)

    second = request(
        manifest,
        clock,
        actuator_name="right_eye_horizontal",
        position=0.20,
    )
    decision = supervisor.authorize(second)

    assert decision.authorized is True
    assert supervisor.record_status(applied_status(second, clock)).accepted
    assert supervisor.last_applied_by_actuator["mouth_open"] == 1_250_000_000
    assert supervisor.last_applied_by_actuator["right_eye_horizontal"] == clock.now_ns


def test_abort_with_pending_request_waits_for_position_reconciliation(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    pending = request(manifest, clock, position=0.10)
    assert supervisor.authorize(pending).authorized

    aborted = supervisor.abort(AbortReason.CAMERA_LOSS)

    assert aborted.recovery_request is None
    assert aborted.recovery_wait is not None
    assert aborted.recovery_wait.reason == "position-reconciliation-required"
    reconciled = supervisor.record_status(applied_status(pending, clock))
    assert supervisor.committed_targets["mouth_open"] == 0.10
    assert (
        reconciled.recovery_request is not None or reconciled.recovery_wait is not None
    )
    assert supervisor.safe_state_verified is False


def test_partial_fault_status_requires_operator_intervention_before_recovery(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    pending = PoseRequest(
        schema_version="pose-request/v1",
        request_id="two-target-request",
        run_id="run-001",
        hardware_id=manifest.hardware_id,
        calibration_sha256=manifest.calibration_sha256,
        issued_monotonic_ns=clock.now_ns,
        expires_monotonic_ns=clock.now_ns + 100_000_000,
        targets=(
            {"actuator_name": "mouth_open", "normalized_position": 0.10},
            {"actuator_name": "right_eye_horizontal", "normalized_position": 0.10},
        ),
    )
    assert supervisor.authorize(pending).authorized
    supervisor.abort(AbortReason.CAMERA_LOSS)
    partial = ActuatorStatus(
        schema_version="actuator-status/v1",
        request_id=pending.request_id,
        run_id=pending.run_id,
        hardware_id=pending.hardware_id,
        calibration_sha256=pending.calibration_sha256,
        reported_monotonic_ns=clock.now_ns,
        state=ActuatorStatusState.FAULT,
        fault_code="partial-write",
        applied_targets=(pending.targets[0],),
    )

    result = supervisor.record_status(partial)

    assert result.accepted is False
    assert result.recovery_request is None
    assert result.recovery_wait is not None
    assert result.recovery_wait.reason == "operator-intervention-required"
    assert supervisor.state is RunState.ABORTING
    assert supervisor.safe_state_verified is False


@pytest.mark.parametrize("stale_at", ["start", "authorize", "watchdog"])
def test_preflight_freshness_is_rechecked_during_runtime_gates(
    manifest: HardwareManifest, clock: FakeClock, stale_at: str
) -> None:
    supervisor = make_supervisor(manifest, clock)
    arm(supervisor, manifest, clock)
    if stale_at == "start":
        clock.advance(20_000_000_000)
        result = supervisor.start()
    else:
        assert supervisor.start().accepted
        clock.advance(20_000_000_000)
        if stale_at == "authorize":
            result = supervisor.authorize(request(manifest, clock))
        else:
            result = supervisor.watchdog(clock.now_ns)

    assert (
        result.accepted is False
        if hasattr(result, "accepted")
        else not result.authorized
    )
    assert result.fault is not None
    assert result.fault.code == "preflight-stale"
    assert supervisor.state is RunState.ABORTING


def test_approval_freshness_is_rechecked_at_start(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    arm(supervisor, manifest, clock)
    clock.advance(10_000_000_000)

    result = supervisor.start()

    assert result.accepted is False
    assert result.fault is not None
    assert result.fault.code == "approval-stale"
    assert result.recovery_request is not None


def test_invalid_transitions_return_rejections_without_destroying_bookkeeping(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)

    start_result = supervisor.start()
    acknowledgement = supervisor.acknowledge_fault(passing_preflight(manifest))
    unavailable = supervisor.recovery_unavailable("not aborting")

    for result in (start_result, acknowledgement, unavailable):
        assert result.accepted is False
        assert result.fault is not None
        assert result.fault.code == "invalid-transition"
    assert supervisor.state is RunState.DISARMED
    assert supervisor.fault is None
    assert supervisor.fault_history == ()


def test_fault_acknowledgement_requires_completed_abort_and_restored_preconditions(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    start(supervisor, manifest, clock)
    supervisor.authorize(request(manifest, clock, position=0.21))

    early = supervisor.acknowledge_fault(passing_preflight(manifest))
    assert early.accepted is False
    assert early.fault is not None
    assert early.fault.code == "invalid-transition"

    faulted(supervisor)
    failed = passing_preflight(manifest).model_copy(
        update={"competing_process_detected": True}
    )
    rejected = supervisor.acknowledge_fault(failed)
    assert rejected.accepted is False
    assert supervisor.state is RunState.FAULTED

    restored = supervisor.acknowledge_fault(passing_preflight(manifest))
    assert restored.accepted is True
    assert supervisor.state is RunState.DISARMED
    assert supervisor.fault is None
    assert [fault.code for fault in supervisor.fault_history] == [
        "step-limit-exceeded",
        "recovery-unavailable",
    ]


def test_preflight_identity_mismatch_fails_closed(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    evidence = passing_preflight(manifest).model_copy(
        update={"controller_serial": "different-controller"}
    )

    result = supervisor.preflight(evidence)

    assert result.accepted is False
    assert result.fault is not None
    assert result.fault.code == "controller-identity-mismatch"
    assert supervisor.state is RunState.FAULTED
