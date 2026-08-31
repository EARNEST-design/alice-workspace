"""Behavioral tests for the deterministic, mock-first actuator adapter."""

from pathlib import Path

import pytest

from alice.contracts.actuation import PoseRequest
from alice.hardware.adapter import ActuatorAuthorizationError, AdapterMode
from alice.hardware.manifest import HardwareManifest, load_manifest
from alice.hardware.mock_adapter import MockActuatorAdapter
from alice.safety.permits import ActuationPermit, PermitKind
from alice.safety.supervisor import (
    AuthorizationDecision,
    RecoveryAuthorization,
    RunState,
)

MANIFEST_PATH = Path(__file__).parents[2] / "hardware" / "alice-face-v1.yaml"


@pytest.fixture
def manifest() -> HardwareManifest:
    return load_manifest(MANIFEST_PATH)


def request(manifest: HardwareManifest, *, position: float = 0.1) -> PoseRequest:
    return PoseRequest(
        schema_version="pose-request/v1",
        request_id=f"request-{position}",
        run_id="run-001",
        hardware_id=manifest.hardware_id,
        calibration_sha256=manifest.calibration_sha256,
        issued_monotonic_ns=1_000,
        expires_monotonic_ns=2_000,
        targets=({"actuator_name": "mouth_open", "normalized_position": position},),
    )


def authorized(request: PoseRequest) -> AuthorizationDecision:
    return AuthorizationDecision(
        authorized=True,
        state=RunState.RUNNING,
        request=request,
        permit=ActuationPermit(issuer_id="test", capability="test-capability"),
    )


class PermissiveVerifier:
    def consume(
        self,
        permit: ActuationPermit,
        *,
        request: PoseRequest,
        kind: PermitKind,
        recovery_sequence_index: int | None = None,
        originating_fault_code: str | None = None,
    ) -> None:
        pass


def mock_adapter(manifest: HardwareManifest) -> MockActuatorAdapter:
    return MockActuatorAdapter(
        manifest=manifest,
        clock=lambda: 1_500,
        permit_verifier=PermissiveVerifier(),
    )


def test_mock_applies_authorized_requests_deterministically(
    manifest: HardwareManifest,
) -> None:
    adapter = mock_adapter(manifest)
    accepted = request(manifest)

    first = adapter.apply(authorized(accepted))
    second = adapter.apply(authorized(accepted))

    assert first == second
    assert first.state.value == "applied"
    assert first.applied_targets == accepted.targets
    assert adapter.positions == {"mouth_open": 0.1}
    assert adapter.identity.backend == "mock"
    assert adapter.identity.mode is AdapterMode.SIMULATION
    assert adapter.identity.hardware_capable is False
    assert first.targets_reached is True
    assert len(first.controller_output_samples) == 1
    sample = first.controller_output_samples[0]
    assert sample.target_qus == manifest.actuator("mouth_open").target_qus(0.1)
    assert sample.observed_qus == sample.target_qus


def test_mock_accepts_recovery_authorization(manifest: HardwareManifest) -> None:
    adapter = mock_adapter(manifest)
    recovery_request = request(manifest, position=0.0)
    authority = RecoveryAuthorization(
        sequence_index=1,
        originating_fault_code="watchdog-expired",
        request=recovery_request,
        permit=ActuationPermit(issuer_id="test", capability="test-recovery"),
    )

    result = adapter.apply(authority)

    assert result.applied_targets == recovery_request.targets


def test_mock_rejects_raw_or_unauthorized_requests(
    manifest: HardwareManifest,
) -> None:
    adapter = mock_adapter(manifest)
    proposed = request(manifest)
    denied = AuthorizationDecision(
        authorized=False,
        state=RunState.ABORTING,
    )

    with pytest.raises(ActuatorAuthorizationError, match="authorization wrapper"):
        adapter.apply(proposed)  # type: ignore[arg-type]
    with pytest.raises(ActuatorAuthorizationError, match="not authorized"):
        adapter.apply(denied)
    assert adapter.positions == {}


def test_mock_rejects_identity_mismatch(manifest: HardwareManifest) -> None:
    adapter = mock_adapter(manifest)
    mismatched = request(manifest).model_copy(update={"hardware_id": "other-face"})

    with pytest.raises(ActuatorAuthorizationError, match="hardware_id mismatch"):
        adapter.apply(authorized(mismatched))
    assert adapter.positions == {}


def test_mock_never_opens_a_transport(manifest: HardwareManifest) -> None:
    opened = False

    def forbidden_transport(*args: object, **kwargs: object) -> object:
        nonlocal opened
        opened = True
        raise AssertionError("mock attempted hardware access")

    adapter = mock_adapter(manifest)
    adapter.apply(authorized(request(manifest)))

    assert opened is False
