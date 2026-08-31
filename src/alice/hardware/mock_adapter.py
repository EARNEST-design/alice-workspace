"""Deterministic, hardware-free actuator adapter used by default."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from alice.contracts.actuation import (
    ActuatorStatus,
    ActuatorStatusState,
    ControllerOutputSample,
)
from alice.hardware.adapter import (
    ActuatorAuthorization,
    AdapterIdentity,
    AdapterMode,
    authorized_request,
)
from alice.hardware.manifest import HardwareManifest
from alice.safety.permits import ActuationPermitVerifier


class MockActuatorAdapter:
    """Record authorized semantic targets without opening or emulating a device."""

    def __init__(
        self,
        *,
        manifest: HardwareManifest,
        clock: Callable[[], int],
        permit_verifier: ActuationPermitVerifier,
    ) -> None:
        self._manifest = manifest
        self._clock = clock
        self._permit_verifier = permit_verifier
        self._positions: dict[str, float] = {}

    @property
    def identity(self) -> AdapterIdentity:
        return AdapterIdentity(
            backend="mock",
            mode=AdapterMode.SIMULATION,
            hardware_capable=False,
        )

    @property
    def positions(self) -> Mapping[str, float]:
        return MappingProxyType(self._positions)

    def apply(self, authorization: ActuatorAuthorization) -> ActuatorStatus:
        now_ns = self._clock()
        request = authorized_request(
            authorization,
            manifest=self._manifest,
            now_monotonic_ns=now_ns,
            permit_verifier=self._permit_verifier,
        )
        self._positions.update(
            {
                target.actuator_name: target.normalized_position
                for target in request.targets
            }
        )
        return ActuatorStatus(
            schema_version="actuator-status/v1",
            request_id=request.request_id,
            run_id=request.run_id,
            hardware_id=request.hardware_id,
            calibration_sha256=request.calibration_sha256,
            reported_monotonic_ns=now_ns,
            state=ActuatorStatusState.APPLIED,
            applied_targets=request.targets,
            controller_output_samples=tuple(
                ControllerOutputSample(
                    actuator_name=target.actuator_name,
                    target_qus=self._manifest.actuator(
                        target.actuator_name
                    ).target_qus(target.normalized_position),
                    observed_qus=self._manifest.actuator(
                        target.actuator_name
                    ).target_qus(target.normalized_position),
                    observed_monotonic_ns=now_ns,
                )
                for target in request.targets
            ),
            targets_reached=True,
        )

    def close(self) -> None:
        """Satisfy the common resource lifecycle; the mock owns no resources."""
