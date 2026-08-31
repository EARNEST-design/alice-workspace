"""Deterministic, hardware-free actuator adapter used by default."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from alice.contracts.actuation import ActuatorStatus, ActuatorStatusState
from alice.hardware.adapter import ActuatorAuthorization, authorized_request
from alice.hardware.manifest import HardwareManifest


class MockActuatorAdapter:
    """Record authorized semantic targets without opening or emulating a device."""

    def __init__(self, *, manifest: HardwareManifest, clock: Callable[[], int]) -> None:
        self._manifest = manifest
        self._clock = clock
        self._positions: dict[str, float] = {}

    @property
    def positions(self) -> Mapping[str, float]:
        return MappingProxyType(self._positions)

    def apply(self, authorization: ActuatorAuthorization) -> ActuatorStatus:
        now_ns = self._clock()
        request = authorized_request(
            authorization,
            manifest=self._manifest,
            now_monotonic_ns=now_ns,
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
        )

    def close(self) -> None:
        """Satisfy the common resource lifecycle; the mock owns no resources."""
