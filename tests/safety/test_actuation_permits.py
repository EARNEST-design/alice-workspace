"""Adversarial tests for single-use supervisor-issued actuation permits."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from alice.contracts.actuation import PoseRequest
from alice.hardware.adapter import ActuatorAuthorizationError
from alice.hardware.manifest import HardwareManifest, load_manifest
from alice.hardware.mock_adapter import MockActuatorAdapter
from alice.safety.permits import ActuationPermit
from alice.safety.supervisor import (
    AbortReason,
    AuthorizationDecision,
    OperatorApproval,
    PreflightEvidence,
    RecoveryAuthorization,
    RunState,
    SafetyLimits,
    SafetySupervisor,
)

MANIFEST_PATH = Path(__file__).parents[2] / "hardware" / "alice-face-v1.yaml"


@dataclass
class Clock:
    now_ns: int = 1_000_000_000

    def __call__(self) -> int:
        return self.now_ns


def running_supervisor(manifest: HardwareManifest, clock: Clock) -> SafetySupervisor:
    supervisor = SafetySupervisor(
        manifest=manifest,
        limits=SafetyLimits(
            max_step=0.2,
            max_rate_per_second=1.0,
            max_acceleration_per_second_squared=10.0,
            watchdog_timeout_ns=1_000_000_000,
            approval_max_age_ns=10_000_000_000,
            preflight_max_age_ns=10_000_000_000,
            command_max_age_ns=500_000_000,
            recovery_command_ttl_ns=500_000_000,
        ),
        clock=clock,
    )
    evidence = PreflightEvidence(
        run_id="run-001",
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
        observed_monotonic_ns=clock.now_ns,
    )
    assert supervisor.preflight(evidence).accepted
    assert supervisor.arm(
        OperatorApproval(
            approval_id="approval-001",
            run_id="run-001",
            confirmed_monotonic_ns=clock.now_ns,
        )
    ).accepted
    assert supervisor.start().accepted
    clock.now_ns += 250_000_000
    return supervisor


def request(
    manifest: HardwareManifest,
    clock: Clock,
    *,
    request_id: str = "request-001",
) -> PoseRequest:
    return PoseRequest(
        schema_version="pose-request/v1",
        request_id=request_id,
        run_id="run-001",
        hardware_id=manifest.hardware_id,
        calibration_sha256=manifest.calibration_sha256,
        issued_monotonic_ns=clock.now_ns,
        expires_monotonic_ns=clock.now_ns + 100_000_000,
        targets=({"actuator_name": "mouth_open", "normalized_position": 0.1},),
    )


def test_forged_wrapper_and_request_mutation_are_rejected_and_burn_permit() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    clock = Clock()
    supervisor = running_supervisor(manifest, clock)
    decision = supervisor.authorize(request(manifest, clock))
    assert decision.permit is not None
    mutated = decision.request.model_copy(update={"request_id": "forged"})
    forged = decision.model_copy(update={"request": mutated})
    adapter = MockActuatorAdapter(
        manifest=manifest,
        clock=clock,
        permit_verifier=supervisor.actuation_permit_verifier,
    )

    with pytest.raises(ActuatorAuthorizationError, match="binding mismatch"):
        adapter.apply(forged)
    with pytest.raises(ActuatorAuthorizationError, match="unknown or consumed"):
        adapter.apply(decision)
    assert adapter.positions == {}


def test_permit_is_single_use_and_cannot_cross_supervisor_instances() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    clock_a = Clock()
    clock_b = Clock()
    supervisor_a = running_supervisor(manifest, clock_a)
    supervisor_b = running_supervisor(manifest, clock_b)
    decision = supervisor_a.authorize(request(manifest, clock_a))
    wrong_adapter = MockActuatorAdapter(
        manifest=manifest,
        clock=clock_b,
        permit_verifier=supervisor_b.actuation_permit_verifier,
    )

    with pytest.raises(ActuatorAuthorizationError, match="unknown or consumed"):
        wrong_adapter.apply(decision)

    right_adapter = MockActuatorAdapter(
        manifest=manifest,
        clock=clock_a,
        permit_verifier=supervisor_a.actuation_permit_verifier,
    )
    assert right_adapter.apply(decision).state.value == "applied"
    with pytest.raises(ActuatorAuthorizationError, match="unknown or consumed"):
        right_adapter.apply(decision)


def test_guessed_capability_cannot_forge_supervisor_authority() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    clock = Clock()
    supervisor = running_supervisor(manifest, clock)
    decision = supervisor.authorize(request(manifest, clock))
    assert decision.permit is not None
    forged = decision.model_copy(
        update={
            "permit": ActuationPermit(
                issuer_id=decision.permit.issuer_id,
                capability="attacker-guessed-capability",
            )
        }
    )
    adapter = MockActuatorAdapter(
        manifest=manifest,
        clock=clock,
        permit_verifier=supervisor.actuation_permit_verifier,
    )

    with pytest.raises(ActuatorAuthorizationError, match="unknown or consumed"):
        adapter.apply(forged)
    assert adapter.apply(decision).state.value == "applied"


def test_normal_permit_cannot_be_relabelled_as_recovery() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    clock = Clock()
    supervisor = running_supervisor(manifest, clock)
    decision = supervisor.authorize(request(manifest, clock))
    assert decision.request is not None
    assert decision.permit is not None
    forged = RecoveryAuthorization(
        sequence_index=1,
        originating_fault_code="operator-request",
        request=decision.request,
        permit=decision.permit,
    )
    adapter = MockActuatorAdapter(
        manifest=manifest,
        clock=clock,
        permit_verifier=supervisor.actuation_permit_verifier,
    )

    with pytest.raises(ActuatorAuthorizationError, match="kind mismatch"):
        adapter.apply(forged)


def test_recovery_permit_cannot_be_relabelled_as_normal() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    clock = Clock()
    supervisor = running_supervisor(manifest, clock)
    result = supervisor.abort(AbortReason.OPERATOR_REQUEST)
    recovery = result.recovery_authorization
    assert recovery is not None
    forged = AuthorizationDecision(
        authorized=True,
        state=RunState.RUNNING,
        request=recovery.request,
        permit=recovery.permit,
    )
    adapter = MockActuatorAdapter(
        manifest=manifest,
        clock=clock,
        permit_verifier=supervisor.actuation_permit_verifier,
    )

    with pytest.raises(ActuatorAuthorizationError, match="kind mismatch"):
        adapter.apply(forged)


@pytest.mark.parametrize(
    "updates",
    [
        {"sequence_index": 999},
        {"originating_fault_code": "different-fault"},
    ],
)
def test_recovery_permit_binds_sequence_and_originating_fault(
    updates: dict[str, object],
) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    clock = Clock()
    supervisor = running_supervisor(manifest, clock)
    result = supervisor.abort(AbortReason.OPERATOR_REQUEST)
    recovery = result.recovery_authorization
    assert recovery is not None
    forged = recovery.model_copy(update=updates)
    adapter = MockActuatorAdapter(
        manifest=manifest,
        clock=clock,
        permit_verifier=supervisor.actuation_permit_verifier,
    )

    with pytest.raises(ActuatorAuthorizationError, match="correlation mismatch"):
        adapter.apply(forged)


def test_supervisor_revokes_unconsumed_permit_when_run_aborts() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    clock = Clock()
    supervisor = running_supervisor(manifest, clock)
    decision = supervisor.authorize(request(manifest, clock))
    supervisor.abort(AbortReason.OPERATOR_REQUEST)
    adapter = MockActuatorAdapter(
        manifest=manifest,
        clock=clock,
        permit_verifier=supervisor.actuation_permit_verifier,
    )

    with pytest.raises(ActuatorAuthorizationError, match="unknown or consumed"):
        adapter.apply(decision)


def test_pydantic_authorization_without_supervisor_permit_is_rejected() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    clock = Clock()
    supervisor = running_supervisor(manifest, clock)
    forged = AuthorizationDecision(
        authorized=True,
        state=RunState.RUNNING,
        request=request(manifest, clock),
    )
    adapter = MockActuatorAdapter(
        manifest=manifest,
        clock=clock,
        permit_verifier=supervisor.actuation_permit_verifier,
    )

    with pytest.raises(ActuatorAuthorizationError, match="permit is missing"):
        adapter.apply(forged)
