"""Authorization-preserving interface shared by actuator backends."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict

from alice.contracts.actuation import ActuatorStatus, PoseRequest
from alice.hardware.manifest import HardwareManifest
from alice.safety.permits import (
    ActuationPermitError,
    ActuationPermitVerifier,
    PermitKind,
)
from alice.safety.supervisor import (
    AuthorizationDecision,
    RecoveryAuthorization,
    RunState,
)

ActuatorAuthorization: TypeAlias = AuthorizationDecision | RecoveryAuthorization


class ActuatorAuthorizationError(ValueError):
    """Raised before forwarding when supervisor authority is absent or invalid."""


class AdapterMode(StrEnum):
    SIMULATION = "simulation"
    HARDWARE = "hardware"


class AdapterIdentity(BaseModel):
    """Immutable runtime backend attestation recorded before a run starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: Literal["mock", "maestro"]
    mode: AdapterMode
    hardware_capable: bool


class ActuatorAdapter(Protocol):
    """An actuator sink that accepts supervisor authority, never raw proposals."""

    @property
    def identity(self) -> AdapterIdentity:
        """Return immutable runtime backend and capability identity."""

    def apply(self, authorization: ActuatorAuthorization) -> ActuatorStatus:
        """Apply one supervisor-authorized request and report confirmed state."""

    def close(self) -> None:
        """Release adapter resources deterministically."""


def authorized_request(
    authorization: ActuatorAuthorization,
    *,
    manifest: HardwareManifest,
    now_monotonic_ns: int,
    permit_verifier: ActuationPermitVerifier,
) -> PoseRequest:
    """Extract and validate a request while retaining the authorization boundary."""

    if isinstance(authorization, AuthorizationDecision):
        if (
            not authorization.authorized
            or authorization.state is not RunState.RUNNING
            or authorization.request is None
        ):
            raise ActuatorAuthorizationError("request is not authorized for RUNNING")
        if authorization.permit is None:
            raise ActuatorAuthorizationError("supervisor actuation permit is missing")
        request = authorization.request
        permit = authorization.permit
        kind = PermitKind.NORMAL
        recovery_sequence_index = None
        originating_fault_code = None
    elif isinstance(authorization, RecoveryAuthorization):
        request = authorization.request
        permit = authorization.permit
        kind = PermitKind.RECOVERY
        recovery_sequence_index = authorization.sequence_index
        originating_fault_code = authorization.originating_fault_code
    else:
        raise ActuatorAuthorizationError(
            "an authorization wrapper (AuthorizationDecision or "
            "RecoveryAuthorization) is required"
        )
    try:
        permit_verifier.consume(
            permit,
            request=request,
            kind=kind,
            recovery_sequence_index=recovery_sequence_index,
            originating_fault_code=originating_fault_code,
        )
        manifest.validate_request(request, now_monotonic_ns=now_monotonic_ns)
    except (ActuationPermitError, ValueError) as exc:
        raise ActuatorAuthorizationError(str(exc)) from exc
    return request
