"""Explicitly enabled, disconnected-by-default Pololu Maestro adapter."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from alice.contracts.actuation import (
    ActuatorStatus,
    ActuatorStatusState,
    ActuatorTarget,
    ControllerOutputSample,
    PoseRequest,
)
from alice.hardware.adapter import (
    ActuatorAuthorization,
    AdapterIdentity,
    AdapterMode,
    authorized_request,
)
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

    def fileno(self) -> int: ...


TransportFactory = Callable[[str, float], SerialTransport]


class MaestroConnectionError(RuntimeError):
    """A connection lifecycle or explicit-enable requirement failed."""


class MaestroPreflightSnapshot(BaseModel):
    """Read-only command-port state; positions are controller outputs, not mechanics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    controller_error_register: Annotated[int, Field(ge=0, le=0xFFFF)]
    positions_qus: dict[str, Annotated[int, Field(ge=0, le=0xFFFF)]]
    observed_monotonic_ns: Annotated[int, Field(ge=0)]


class MaestroStreamReceipt(BaseModel):
    """A completed serial write plus current PWM observation, never APPLIED."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    state: Literal["sent"] = "sent"
    target_qus: int
    observed_qus: int
    sent_monotonic_ns: int
    reported_monotonic_ns: int


class _TransportFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        samples: tuple[ControllerOutputSample, ...] = (),
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.samples = samples


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
    """Advanced low-level adapter; use only from the trusted hardware root.

    Construction is disconnected, but this class is not an in-process security
    boundary. Application and experiment code must use the reviewed staged
    hardware-identification or mouth-only speech composition instead of
    instantiating it directly.
    """

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
        self.jaw_response_override: dict[str, object] | None = None

    @property
    def identity(self) -> AdapterIdentity:
        return AdapterIdentity(
            backend="maestro",
            mode=AdapterMode.HARDWARE,
            hardware_capable=True,
        )

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
        controller_samples: list[ControllerOutputSample] = []
        current_name = request.targets[0].actuator_name
        try:
            for target in request.targets:
                current_name = target.actuator_name
                definition = self._manifest.actuator(current_name)
                target_qus = definition.target_qus(target.normalized_position)
                self._write_all(encode_set_target(definition.channel, target_qus))
                controller_samples.extend(
                    self._wait_for_target(
                        actuator_name=current_name,
                        channel=definition.channel,
                        target_qus=target_qus,
                    )
                )
                confirmed.append(target)
            self._write_all(encode_get_errors())
            errors = parse_error_register(self._read_exact(2))
            if errors:
                status = self._status(
                    request,
                    state=ActuatorStatusState.FAULT,
                    confirmed=confirmed,
                    controller_samples=controller_samples,
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
            controller_samples.extend(exc.samples)
            status = self._status(
                request,
                state=ActuatorStatusState.FAULT,
                confirmed=confirmed,
                controller_samples=controller_samples,
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
            controller_samples=controller_samples,
        )

    def read_only_preflight(
        self, actuator_names: tuple[str, ...]
    ) -> MaestroPreflightSnapshot:
        """Read errors and commanded output positions without issuing Set Target."""

        if self._transport is None:
            raise MaestroConnectionError("adapter is not open")
        try:
            self._write_all(encode_get_errors())
            errors = parse_error_register(self._read_exact(2))
            positions: dict[str, int] = {}
            for name in actuator_names:
                definition = self._manifest.actuator(name)
                self._write_all(encode_get_position(definition.channel))
                positions[name] = parse_position(self._read_exact(2))
        except _TransportFailure as exc:
            self._poison_transport()
            raise MaestroConnectionError(
                f"read-only controller preflight failed: {exc.detail}"
            ) from exc
        return MaestroPreflightSnapshot(
            controller_error_register=errors,
            positions_qus=positions,
            observed_monotonic_ns=self._clock(),
        )

    def initialize_disabled_jaw_home(
        self, explicit_enable_token: str
    ) -> MaestroPreflightSnapshot:
        """Enable the fixed speech jaw at Home before supervisor startup.

        This is a narrowly scoped startup command under explicit hardware
        enablement, not a normal trajectory permit. Disabled PWM says nothing
        about mechanical pose; do not claim a bounded velocity for this first
        enable. The attended speech root records it separately from playback.
        """
        if explicit_enable_token != self._enable_token:
            raise MaestroConnectionError("explicit enable token does not match")
        definitions = self._manifest.actuators
        if (
            self._manifest.hardware_id != "alice-jaw-speech-trial-v1"
            or len(definitions) != 1
            or definitions[0].name != "mouth_open"
            or definitions[0].channel != 6
            or definitions[0].home_qus != 5059
        ):
            raise MaestroConnectionError("initialization requires the fixed jaw scope")
        before = self.read_only_preflight(("mouth_open",))
        if before.controller_error_register or before.positions_qus["mouth_open"] != 0:
            raise MaestroConnectionError(
                "jaw initialization requires disabled, error-free output"
            )
        try:
            self._write_all(encode_set_target(6, 5059))
            self._wait_for_target(
                actuator_name="mouth_open", channel=6, target_qus=5059
            )
            self._sleeper(0.25)
            after = self.read_only_preflight(("mouth_open",))
            if (
                after.controller_error_register
                or after.positions_qus["mouth_open"] != 5059
            ):
                raise MaestroConnectionError(
                    "jaw initialization output/error check failed"
                )
            return after
        except BaseException as exc:
            self._poison_transport()
            if isinstance(exc, (_TransportFailure, MaestroConnectionError)):
                raise MaestroConnectionError(
                    f"jaw initialization failed: {exc}"
                ) from exc
            raise

    def stream_jaw_target(
        self, explicit_enable_token: str, request: PoseRequest
    ) -> MaestroStreamReceipt:
        """Speech-only target streaming; observe PWM without waiting for arrival.

        The trusted speech stream bounds target kinematics before each call.
        This method enforces the fixed channel, calibration, expiry and token.
        The settled-output apply/permit API remains unchanged for other callers.
        """
        if explicit_enable_token != self._enable_token:
            raise MaestroConnectionError("explicit enable token does not match")
        if (
            self._manifest.hardware_id != "alice-jaw-speech-trial-v1"
            or len(self._manifest.actuators) != 1
            or self._manifest.actuators[0].name != "mouth_open"
            or self._manifest.actuators[0].channel != 6
            or len(request.targets) != 1
            or request.targets[0].actuator_name != "mouth_open"
        ):
            raise MaestroConnectionError("streaming requires the fixed jaw scope")
        self._manifest.validate_request(request, now_monotonic_ns=self._clock())
        if self._transport is None or self._poisoned:
            raise MaestroConnectionError("streaming adapter is not open")
        target = self._manifest.actuator("mouth_open").target_qus(
            request.targets[0].normalized_position
        )
        try:
            payload = encode_set_target(6, target)
            # This is the host write-start timestamp, not readback completion or
            # measured physical motion. The planner reserves this 2 ms window.
            sent_ns = self._clock()
            if not 0 <= sent_ns - request.issued_monotonic_ns <= 2_000_000:
                raise MaestroConnectionError("streaming dispatch deadline missed")
            self._write_all(payload)
            self._write_all(encode_get_errors())
            errors = parse_error_register(self._read_exact(2))
            self._write_all(encode_get_position(6))
            observed = parse_position(self._read_exact(2))
            definition = self._manifest.actuator("mouth_open")
            if (
                errors
                or not definition.software_min_qus
                <= observed
                <= definition.software_max_qus
            ):
                raise MaestroConnectionError(
                    f"streaming controller error={errors}; output={observed}"
                )
            return MaestroStreamReceipt(
                target_qus=target,
                observed_qus=observed,
                sent_monotonic_ns=sent_ns,
                reported_monotonic_ns=self._clock(),
            )
        except BaseException:
            self._poison_transport()
            raise

    def enable_fast_jaw_response(self, explicit_enable_token: str) -> None:
        """Temporarily remove channel 6 firmware ramping, preserving EEPROM.

        The reviewed Alice profile is speed 0 / acceleration 11. Restore that
        profile at normal completion; faults record non-restoration.
        Software trajectory bounds remain the speech stream's responsibility.
        """
        if explicit_enable_token != self._enable_token:
            raise MaestroConnectionError("explicit enable token does not match")
        definitions = self._manifest.actuators
        if (
            self._manifest.hardware_id != "alice-jaw-speech-trial-v1"
            or len(definitions) != 1
            or definitions[0].name != "mouth_open"
            or definitions[0].channel != 6
            or definitions[0].home_qus != 5059
            or self.jaw_response_override is not None
        ):
            raise MaestroConnectionError("fast response requires the fixed jaw scope")
        before = self.read_only_preflight(("mouth_open",))
        if (
            before.controller_error_register
            or before.positions_qus["mouth_open"] != 5059
        ):
            raise MaestroConnectionError("fast response requires error-free jaw Home")
        self.jaw_response_override = {
            "channel": 6,
            "speed": 0,
            "acceleration": 0,
            "restore_speed": 0,
            "restore_acceleration": 11,
            "persistent_settings_changed": False,
            "status": "requested",
        }
        try:
            self._set_jaw_response(acceleration=0)
            self.jaw_response_override["status"] = "active"
        except BaseException:
            self._poison_transport()
            raise

    def _set_jaw_response(self, *, acceleration: int) -> None:
        # Pololu compact protocol: Set Speed 0x87 / Set Acceleration 0x89.
        # These fixed runtime commands never set a position or write EEPROM.
        self._write_all(bytes([0x87, 6, 0, 0]))
        self._write_all(bytes([0x89, 6, acceleration, 0]))
        self._write_all(encode_get_errors())
        errors = parse_error_register(self._read_exact(2))
        if errors:
            raise MaestroConnectionError(f"jaw response controller error={errors}")

    def fileno(self) -> int:
        """Return the opened transport fd for minimal post-fork detachment."""

        transport = self._transport
        if transport is None:
            raise MaestroConnectionError("adapter is not open")
        try:
            fd = transport.fileno()
        except Exception as exc:
            raise MaestroConnectionError(
                "opened transport has no usable raw OS file descriptor"
            ) from exc
        if type(fd) is not int or fd < 0:
            raise MaestroConnectionError(
                "transport raw OS file descriptor must be a nonnegative integer"
            )
        return fd

    def _wait_for_target(
        self, *, actuator_name: str, channel: int, target_qus: int
    ) -> tuple[ControllerOutputSample, ...]:
        deadline_ns = self._clock() + self._settle_timeout_ns
        max_polls = self._settle_timeout_ns // self._poll_interval_ns + 2
        last_observed: int | None = None
        samples: list[ControllerOutputSample] = []
        for _ in range(max_polls):
            self._write_all(encode_get_position(channel))
            last_observed = parse_position(self._read_exact(2))
            samples.append(
                ControllerOutputSample(
                    actuator_name=actuator_name,
                    observed_qus=last_observed,
                    target_qus=target_qus,
                    observed_monotonic_ns=self._clock(),
                )
            )
            if last_observed == target_qus:
                return tuple(samples)
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
            samples=tuple(samples),
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
        controller_samples: list[ControllerOutputSample],
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
            controller_output_samples=tuple(controller_samples),
            targets_reached=(
                len(confirmed) == len(request.targets)
                and all(
                    any(
                        sample.actuator_name == target.actuator_name
                        and sample.observed_qus == sample.target_qus
                        for sample in controller_samples
                    )
                    for target in confirmed
                )
            ),
        )

    def restore_jaw_response(self) -> None:
        """Restore the reviewed profile on the serial-owner thread at completion.

        Never call this from watchdog/fault cleanup: it performs serial I/O.
        """
        if self.jaw_response_override is None:
            return
        if self.jaw_response_override["status"] != "active":
            raise MaestroConnectionError("jaw response override is no longer active")
        try:
            self._set_jaw_response(acceleration=11)
            self.jaw_response_override["status"] = "restored"
        except BaseException:
            self.jaw_response_override["status"] = "restoration-failed"
            self._poison_transport()
            raise

    def close(self) -> None:
        # Watchdog may call this while the serial owner is in a transaction.
        # Detach only: adding restoration writes/reads here corrupts framing.
        transport, self._transport = self._transport, None
        if self.jaw_response_override is not None and self.jaw_response_override[
            "status"
        ] in ("requested", "active"):
            self.jaw_response_override["status"] = "not-restored-close"
        if transport is not None:
            try:
                transport.close()
            except Exception as exc:
                raise MaestroConnectionError(f"serial close failed: {exc}") from exc

    def _poison_transport(self) -> None:
        """Make an ambiguous serial session permanently unusable."""

        self._poisoned = True
        if self.jaw_response_override is not None and self.jaw_response_override[
            "status"
        ] in ("requested", "active"):
            self.jaw_response_override["status"] = "not-restored-transport-fault"
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
