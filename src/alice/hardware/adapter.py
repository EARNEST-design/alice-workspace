"""Authorization-preserving interface shared by actuator backends."""

from __future__ import annotations

from typing import Protocol, TypeAlias

from alice.contracts.actuation import ActuatorStatus, PoseRequest
from alice.hardware.manifest import HardwareManifest
from alice.safety.supervisor import (
    AuthorizationDecision,
    RecoveryAuthorization,
    RunState,
)

ActuatorAuthorization: TypeAlias = AuthorizationDecision | RecoveryAuthorization


class ActuatorAuthorizationError(ValueError):
    """Raised before forwarding when supervisor authority is absent or invalid."""


class ActuatorAdapter(Protocol):
    """An actuator sink that accepts supervisor authority, never raw proposals."""

    def apply(self, authorization: ActuatorAuthorization) -> ActuatorStatus:
        """Apply one supervisor-authorized request and report confirmed state."""

    def close(self) -> None:
        """Release adapter resources deterministically."""


def authorized_request(
    authorization: ActuatorAuthorization,
    *,
    manifest: HardwareManifest,
    now_monotonic_ns: int,
) -> PoseRequest:
    """Extract and validate a request while retaining the authorization boundary."""

    if isinstance(authorization, AuthorizationDecision):
        if (
            not authorization.authorized
            or authorization.state is not RunState.RUNNING
            or authorization.request is None
        ):
            raise ActuatorAuthorizationError("request is not authorized for RUNNING")
        request = authorization.request
    elif isinstance(authorization, RecoveryAuthorization):
        request = authorization.request
    else:
        raise ActuatorAuthorizationError(
            "an authorization wrapper (AuthorizationDecision or "
            "RecoveryAuthorization) is required"
        )
    try:
        manifest.validate_request(request, now_monotonic_ns=now_monotonic_ns)
    except ValueError as exc:
        raise ActuatorAuthorizationError(str(exc)) from exc
    return request
