"""Deterministic, hardware-free actuator adapter used by default."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class MockAdapterScript(BaseModel):
    """Deterministic mock-only fault data; it cannot select another backend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fault_on_calls: tuple[int, ...] = ()
    raise_after_authorization_calls: tuple[int, ...] = ()
    controller_output_offset_qus_by_call: Mapping[int, int] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def validate_calls(self) -> MockAdapterScript:
        calls = (*self.fault_on_calls, *self.raise_after_authorization_calls)
        if any(call <= 0 for call in calls):
            raise ValueError("mock scripted call numbers must be positive")
        if len(calls) != len(set(calls)):
            raise ValueError("mock fault and raise call numbers must be unique")
        if any(call <= 0 for call in self.controller_output_offset_qus_by_call):
            raise ValueError("mock output-offset call numbers must be positive")
        return self


class MockActuatorAdapter:
    """Record authorized semantic targets without opening or emulating a device."""

    def __init__(
        self,
        *,
        manifest: HardwareManifest,
        clock: Callable[[], int],
        permit_verifier: ActuationPermitVerifier,
        script: MockAdapterScript | None = None,
    ) -> None:
        self._manifest = manifest
        self._clock = clock
        self._permit_verifier = permit_verifier
        self._script = script or MockAdapterScript()
        self._apply_count = 0
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
        self._apply_count += 1
        if self._apply_count in self._script.raise_after_authorization_calls:
            raise TimeoutError("scripted mock application state is unknown")
        self._positions.update(
            {
                target.actuator_name: target.normalized_position
                for target in request.targets
            }
        )
        offset_qus = self._script.controller_output_offset_qus_by_call.get(
            self._apply_count, 0
        )
        samples = tuple(
            ControllerOutputSample(
                actuator_name=target.actuator_name,
                target_qus=self._manifest.actuator(target.actuator_name).target_qus(
                    target.normalized_position
                ),
                observed_qus=(
                    self._manifest.actuator(target.actuator_name).target_qus(
                        target.normalized_position
                    )
                    + offset_qus
                ),
                observed_monotonic_ns=now_ns,
            )
            for target in request.targets
        )
        scripted_fault = self._apply_count in self._script.fault_on_calls
        return ActuatorStatus(
            schema_version="actuator-status/v1",
            request_id=request.request_id,
            run_id=request.run_id,
            hardware_id=request.hardware_id,
            calibration_sha256=request.calibration_sha256,
            reported_monotonic_ns=now_ns,
            state=(
                ActuatorStatusState.FAULT
                if scripted_fault
                else ActuatorStatusState.APPLIED
            ),
            applied_targets=request.targets,
            fault_code="scripted-mock-controller-fault" if scripted_fault else None,
            detail="deterministic mock fault" if scripted_fault else None,
            controller_output_samples=samples,
            targets_reached=True,
        )

    def close(self) -> None:
        """Satisfy the common resource lifecycle; the mock owns no resources."""
