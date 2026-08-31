"""Transition-table tests for the hardware-independent safety supervisor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from alice.contracts.actuation import ActuatorStatus, ActuatorStatusState, PoseRequest
from alice.hardware.manifest import HardwareManifest, load_manifest
from alice.safety.supervisor import (
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
    )


def make_supervisor(manifest: HardwareManifest, clock: FakeClock) -> SafetySupervisor:
    return SafetySupervisor(
        manifest=manifest,
        limits=SafetyLimits(
            max_step=0.20,
            max_rate_per_second=0.50,
            watchdog_timeout_ns=500_000_000,
            approval_max_age_ns=1_000_000_000,
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
        targets=({"actuator_name": "mouth_open", "normalized_position": position},),
    )


def arm(
    supervisor: SafetySupervisor, manifest: HardwareManifest, clock: FakeClock
) -> None:
    assert supervisor.preflight(passing_preflight(manifest)).accepted
    assert supervisor.state is RunState.PREFLIGHT
    assert supervisor.arm(approval(clock)).accepted
    assert supervisor.state is RunState.ARMED


def faulted(supervisor: SafetySupervisor) -> None:
    assert supervisor.state is RunState.ABORTING
    supervisor.complete_abort()
    assert supervisor.state is RunState.FAULTED


def test_happy_path_follows_disarmed_preflight_armed_running(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)

    arm(supervisor, manifest, clock)
    decision = supervisor.authorize(request(manifest, clock))

    assert decision.authorized is True
    assert decision.request is not None
    assert decision.fault is None
    assert supervisor.state is RunState.RUNNING


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
        ({"issued_monotonic_ns": 1_000_000_001}, "request-issued-in-future"),
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
    arm(supervisor, manifest, clock)

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
    arm(supervisor, manifest, clock)
    assert supervisor.authorize(request(manifest, clock, position=0.10)).authorized
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
    arm(supervisor, manifest, clock)
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
    assert result.fault.code == "controller-error"
    assert supervisor.state is RunState.ABORTING


def test_watchdog_expiry_aborts_at_deadline(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    arm(supervisor, manifest, clock)
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
    arm(supervisor, manifest, clock)
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
    arm(supervisor, manifest, clock)
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


def test_fault_acknowledgement_requires_completed_abort_and_restored_preconditions(
    manifest: HardwareManifest, clock: FakeClock
) -> None:
    supervisor = make_supervisor(manifest, clock)
    arm(supervisor, manifest, clock)
    supervisor.authorize(request(manifest, clock, position=0.21))

    with pytest.raises(RuntimeError, match="FAULTED"):
        supervisor.acknowledge_fault(passing_preflight(manifest))

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
