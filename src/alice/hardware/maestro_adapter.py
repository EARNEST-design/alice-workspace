"""Explicitly enabled, disconnected-by-default Pololu Maestro adapter."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from alice.contracts.actuation import (
    ActuatorStatus,
    ActuatorStatusState,
    ActuatorTarget,
    PoseRequest,
)
from alice.hardware.adapter import ActuatorAuthorization, authorized_request
from alice.hardware.maestro_protocol import (
    encode_get_errors,
    encode_get_position,
    encode_set_target,
    parse_error_register,
    parse_position,
)
from alice.hardware.manifest import HardwareManifest
from alice.safety.permits import ActuationPermitVerifier


class SerialTransport(Protocol):
    def write(self, data: bytes) -> int | None: ...

    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


TransportFactory = Callable[[str, float], SerialTransport]


class MaestroConnectionError(RuntimeError):
    """A connection lifecycle or explicit-enable requirement failed."""


class _TransportFailure(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _default_transport_factory(path: str, timeout_seconds: float) -> SerialTransport:
    # Import is deliberately delayed until an explicitly enabled open().
    import serial

    return serial.Serial(
        port=path,
        baudrate=9600,
        timeout=timeout_seconds,
        write_timeout=timeout_seconds,
    )


class MaestroAdapter:
    """Maestro adapter which cannot connect during import or construction."""

    def __init__(
        self,
        *,
        manifest: HardwareManifest,
        stable_device_path: str,
        expected_controller_serial: str,
        required_enable_token: str,
        clock: Callable[[], int],
        permit_verifier: ActuationPermitVerifier,
        transport_factory: TransportFactory = _default_transport_factory,
        timeout_seconds: float = 0.25,
        settle_timeout_ns: int = 500_000_000,
        poll_interval_ns: int = 20_000_000,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not required_enable_token:
            raise ValueError("required enable token cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if settle_timeout_ns <= 0 or poll_interval_ns <= 0:
            raise ValueError("settling timeout and poll interval must be positive")
        self._manifest = manifest
        self._path = stable_device_path
        self._expected_serial = expected_controller_serial
        self._enable_token = required_enable_token
        self._clock = clock
        self._permit_verifier = permit_verifier
        self._transport_factory = transport_factory
        self._timeout_seconds = timeout_seconds
        self._settle_timeout_ns = settle_timeout_ns
        self._poll_interval_ns = poll_interval_ns
        self._sleeper = sleeper
        self._transport: SerialTransport | None = None
        self._poisoned = False

    @property
    def is_open(self) -> bool:
        return self._transport is not None

    @property
    def is_poisoned(self) -> bool:
        return self._poisoned

    def open(self, explicit_enable_token: str) -> None:
        if self._poisoned:
            raise MaestroConnectionError(
                "adapter is poisoned; construct a fresh adapter after new preflight"
            )
        if self._transport is not None:
            raise MaestroConnectionError("adapter is already open")
        device = Path(self._path)
        if not device.is_absolute() or device.parent != Path("/dev/serial/by-id"):
            raise MaestroConnectionError(
                "a stable /dev/serial/by-id device path is required"
            )
        manifest_serial = self._manifest.controller.serial_number
        if self._expected_serial != manifest_serial:
            raise MaestroConnectionError(
                "expected controller serial does not match the hardware manifest"
            )
        if self._path != self._manifest.controller.command_device_path:
            raise MaestroConnectionError(
                "device path does not exactly match the reviewed command path"
            )
        if explicit_enable_token != self._enable_token:
            raise MaestroConnectionError("explicit enable token does not match")
        try:
            transport = self._transport_factory(self._path, self._timeout_seconds)
        except Exception as exc:
            raise MaestroConnectionError(f"serial open failed: {exc}") from exc
        self._transport = transport

    def apply(self, authorization: ActuatorAuthorization) -> ActuatorStatus:
        now_ns = self._clock()
        request = authorized_request(
            authorization,
            manifest=self._manifest,
            now_monotonic_ns=now_ns,
            permit_verifier=self._permit_verifier,
        )
        transport = self._transport
        if transport is None:
            raise MaestroConnectionError("adapter is not open")
        confirmed: list[ActuatorTarget] = []
        current_name = request.targets[0].actuator_name
        try:
            for target in request.targets:
                current_name = target.actuator_name
                definition = self._manifest.actuator(current_name)
                target_qus = definition.target_qus(target.normalized_position)
                self._write_all(encode_set_target(definition.channel, target_qus))
                self._wait_for_target(
                    actuator_name=current_name,
                    channel=definition.channel,
                    target_qus=target_qus,
                )
                confirmed.append(target)
            self._write_all(encode_get_errors())
            errors = parse_error_register(self._read_exact(2))
            if errors:
                status = self._status(
                    request,
                    state=ActuatorStatusState.FAULT,
                    confirmed=confirmed,
                    fault_code=f"maestro-error-register-0x{errors:04x}",
                    detail=(
                        f"Maestro error register reported 0x{errors:04x}; "
                        "confirmed targets describe controller command output, "
                        "not mechanical position"
                    ),
                )
                self._poison_transport()
                return status
        except _TransportFailure as exc:
            status = self._status(
                request,
                state=ActuatorStatusState.FAULT,
                confirmed=confirmed,
                fault_code=exc.code,
                detail=(
                    f"{current_name}: {exc.detail}; controller command state is "
                    "unknown for the unconfirmed target and mechanical position "
                    "requires independent verification"
                ),
            )
            self._poison_transport()
            return status
        return self._status(
            request,
            state=ActuatorStatusState.APPLIED,
            confirmed=confirmed,
        )

    def _wait_for_target(
        self, *, actuator_name: str, channel: int, target_qus: int
    ) -> None:
        deadline_ns = self._clock() + self._settle_timeout_ns
        max_polls = self._settle_timeout_ns // self._poll_interval_ns + 2
        last_observed: int | None = None
        for _ in range(max_polls):
            self._write_all(encode_get_position(channel))
            last_observed = parse_position(self._read_exact(2))
            if last_observed == target_qus:
                return
            if self._clock() >= deadline_ns:
                break
            try:
                self._sleeper(self._poll_interval_ns / 1_000_000_000)
            except Exception as exc:
                raise _TransportFailure(
                    "settle-wait-error", f"settling wait failed: {exc}"
                ) from exc
        raise _TransportFailure(
            "position-settle-timeout",
            f"{actuator_name} controller output remained at {last_observed}, "
            f"expected {target_qus} before the settling deadline",
        )

    def _write_all(self, payload: bytes) -> None:
        assert self._transport is not None
        sent = 0
        while sent < len(payload):
            try:
                count = self._transport.write(payload[sent:])
            except Exception as exc:
                raise _TransportFailure(
                    "serial-write-error",
                    f"write failed after {sent}/{len(payload)} bytes: {exc}",
                ) from exc
            if count is None or count <= 0 or count > len(payload) - sent:
                raise _TransportFailure(
                    "serial-write-timeout",
                    f"write stopped after {sent}/{len(payload)} bytes",
                )
            if count < len(payload) - sent:
                raise _TransportFailure(
                    "serial-partial-write",
                    f"partial write sent {count}/{len(payload) - sent} bytes; "
                    "protocol framing is ambiguous",
                )
            sent += count

    def _read_exact(self, size: int) -> bytes:
        assert self._transport is not None
        result = bytearray()
        while len(result) < size:
            try:
                chunk = self._transport.read(size - len(result))
            except Exception as exc:
                raise _TransportFailure(
                    "serial-read-error",
                    f"read failed after {len(result)}/{size} bytes: {exc}",
                ) from exc
            if not chunk:
                raise _TransportFailure(
                    "serial-read-timeout",
                    f"read stopped after {len(result)}/{size} bytes",
                )
            if len(chunk) > size - len(result):
                raise _TransportFailure(
                    "serial-read-overflow",
                    "transport returned more bytes than requested",
                )
            result.extend(chunk)
        return bytes(result)

    def _status(
        self,
        request: PoseRequest,
        *,
        state: ActuatorStatusState,
        confirmed: list[ActuatorTarget],
        fault_code: str | None = None,
        detail: str | None = None,
    ) -> ActuatorStatus:
        return ActuatorStatus(
            schema_version="actuator-status/v1",
            request_id=request.request_id,
            run_id=request.run_id,
            hardware_id=request.hardware_id,
            calibration_sha256=request.calibration_sha256,
            reported_monotonic_ns=self._clock(),
            state=state,
            applied_targets=tuple(confirmed),
            fault_code=fault_code,
            detail=detail,
        )

    def close(self) -> None:
        transport, self._transport = self._transport, None
        if transport is not None:
            try:
                transport.close()
            except Exception as exc:
                raise MaestroConnectionError(f"serial close failed: {exc}") from exc

    def _poison_transport(self) -> None:
        """Make an ambiguous serial session permanently unusable."""

        self._poisoned = True
        transport, self._transport = self._transport, None
        if transport is not None:
            try:
                transport.close()
            except Exception:
                # Cleanup must not replace the primary protocol/controller fault.
                pass

    def __enter__(self) -> MaestroAdapter:
        if not self.is_open:
            raise MaestroConnectionError("adapter is not open")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
