"""Opaque, single-use capabilities at the safety-to-actuation boundary."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Protocol

from pydantic import BaseModel, ConfigDict, SecretStr

from alice.contracts.actuation import PoseRequest


class PermitKind(StrEnum):
    NORMAL = "normal"
    RECOVERY = "recovery"


class ActuationPermit(BaseModel):
    """Opaque handle whose authority exists only in its issuing registry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    issuer_id: str
    capability: SecretStr


class ActuationPermitError(ValueError):
    """A permit was absent, forged, replayed, or changed after issuance."""


class ActuationPermitVerifier(Protocol):
    """Consume-only view injected into adapters; it cannot issue authority."""

    def consume(
        self,
        permit: ActuationPermit,
        *,
        request: PoseRequest,
        kind: PermitKind,
        recovery_sequence_index: int | None = None,
        originating_fault_code: str | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class _PermitRecord:
    issuer_id: str
    request_sha256: str
    run_id: str
    kind: PermitKind
    recovery_sequence_index: int | None
    originating_fault_code: str | None


def _request_sha256(request: PoseRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class _PermitConsumer:
    def __init__(self, registry: _PermitRegistry) -> None:
        self.__registry = registry

    def consume(
        self,
        permit: ActuationPermit,
        *,
        request: PoseRequest,
        kind: PermitKind,
        recovery_sequence_index: int | None = None,
        originating_fault_code: str | None = None,
    ) -> None:
        self.__registry.consume(
            permit,
            request=request,
            kind=kind,
            recovery_sequence_index=recovery_sequence_index,
            originating_fault_code=originating_fault_code,
        )


class _PermitRegistry:
    """Issue authority retained by one supervisor instance."""

    def __init__(self) -> None:
        self.__issuer_id = secrets.token_urlsafe(24)
        self.__records: dict[str, _PermitRecord] = {}
        self.__lock = Lock()
        self.__consumer = _PermitConsumer(self)

    @property
    def consumer(self) -> ActuationPermitVerifier:
        return self.__consumer

    def issue(
        self,
        *,
        request: PoseRequest,
        kind: PermitKind,
        recovery_sequence_index: int | None = None,
        originating_fault_code: str | None = None,
    ) -> ActuationPermit:
        if kind is PermitKind.NORMAL:
            if (
                recovery_sequence_index is not None
                or originating_fault_code is not None
            ):
                raise ValueError("normal permit cannot carry recovery correlation")
        elif recovery_sequence_index is None or originating_fault_code is None:
            raise ValueError("recovery permit requires complete correlation")
        with self.__lock:
            capability = secrets.token_urlsafe(32)
            while capability in self.__records:
                capability = secrets.token_urlsafe(32)
            self.__records[capability] = _PermitRecord(
                issuer_id=self.__issuer_id,
                request_sha256=_request_sha256(request),
                run_id=request.run_id,
                kind=kind,
                recovery_sequence_index=recovery_sequence_index,
                originating_fault_code=originating_fault_code,
            )
        return ActuationPermit(
            issuer_id=self.__issuer_id,
            capability=SecretStr(capability),
        )

    def consume(
        self,
        permit: ActuationPermit,
        *,
        request: PoseRequest,
        kind: PermitKind,
        recovery_sequence_index: int | None,
        originating_fault_code: str | None,
    ) -> None:
        capability = permit.capability.get_secret_value()
        with self.__lock:
            record = self.__records.pop(capability, None)
        if record is None or permit.issuer_id != self.__issuer_id:
            raise ActuationPermitError("permit is unknown or consumed")
        if record.issuer_id != self.__issuer_id:
            raise ActuationPermitError("permit supervisor-instance mismatch")
        if record.kind is not kind:
            raise ActuationPermitError("permit kind mismatch")
        if record.run_id != request.run_id or record.request_sha256 != _request_sha256(
            request
        ):
            raise ActuationPermitError("permit request binding mismatch")
        if (
            record.recovery_sequence_index != recovery_sequence_index
            or record.originating_fault_code != originating_fault_code
        ):
            raise ActuationPermitError("permit recovery correlation mismatch")

    def revoke_all(self) -> None:
        with self.__lock:
            self.__records.clear()
