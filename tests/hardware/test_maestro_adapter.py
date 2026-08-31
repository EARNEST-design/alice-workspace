"""Disconnected tests for the guarded Maestro serial adapter."""

from collections import deque
from pathlib import Path
from typing import cast

import pytest
import serial

from alice.contracts.actuation import ActuatorStatusState, PoseRequest
from alice.hardware.adapter import ActuatorAuthorizationError
from alice.hardware.maestro_adapter import (
    MaestroAdapter,
    MaestroConnectionError,
    SerialTransport,
)
from alice.hardware.maestro_protocol import (
    encode_get_errors,
    encode_get_position,
    encode_set_target,
)
from alice.hardware.manifest import HardwareManifest, load_manifest
from alice.safety.supervisor import AuthorizationDecision, RunState

MANIFEST_PATH = Path(__file__).parents[2] / "hardware" / "alice-face-v1.yaml"
DEVICE = "/dev/serial/by-id/usb-Pololu_Corporation_Maestro_00037376-if00"
ENABLE = "enable-run-001"


class FakeSerial:
    def __init__(
        self,
        *,
        reads: tuple[bytes, ...] = (),
        write_sizes: tuple[int, ...] = (),
    ) -> None:
        self.reads = deque(reads)
        self.write_sizes = deque(write_sizes)
        self.written = bytearray()
        self.closed = False

    def write(self, data: bytes) -> int:
        count = self.write_sizes.popleft() if self.write_sizes else len(data)
        self.written.extend(data[:count])
        return count

    def read(self, size: int) -> bytes:
        if not self.reads:
            return b""
        chunk = self.reads.popleft()
        assert len(chunk) <= size
        return chunk

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def manifest() -> HardwareManifest:
    return load_manifest(MANIFEST_PATH)


def request(
    manifest: HardwareManifest,
    *,
    targets: tuple[dict[str, object], ...] | None = None,
) -> PoseRequest:
    return PoseRequest(
        schema_version="pose-request/v1",
        request_id="request-001",
        run_id="run-001",
        hardware_id=manifest.hardware_id,
        calibration_sha256=manifest.calibration_sha256,
        issued_monotonic_ns=1_000,
        expires_monotonic_ns=2_000,
        targets=targets
        or ({"actuator_name": "mouth_open", "normalized_position": 0.0},),
    )


def authorized(request: PoseRequest) -> AuthorizationDecision:
    return AuthorizationDecision(
        authorized=True,
        state=RunState.RUNNING,
        request=request,
    )


def adapter(
    manifest: HardwareManifest,
    fake: FakeSerial,
    *,
    path: str = DEVICE,
    expected_serial: str = "00037376",
) -> MaestroAdapter:
    return MaestroAdapter(
        manifest=manifest,
        stable_device_path=path,
        expected_controller_serial=expected_serial,
        required_enable_token=ENABLE,
        clock=lambda: 1_500,
        transport_factory=lambda _path, _timeout: cast(SerialTransport, fake),
        timeout_seconds=0.1,
    )


def test_import_and_construction_never_open_serial(
    manifest: HardwareManifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def forbidden_serial(*_args: object, **_kwargs: object) -> SerialTransport:
        nonlocal calls
        calls += 1
        raise AssertionError("serial constructor called without explicit open")

    monkeypatch.setattr(serial, "Serial", forbidden_serial)
    instance = MaestroAdapter(
        manifest=manifest,
        stable_device_path=DEVICE,
        expected_controller_serial="00037376",
        required_enable_token=ENABLE,
        clock=lambda: 1_500,
    )

    assert calls == 0
    assert instance.is_open is False


@pytest.mark.parametrize(
    ("path", "serial", "token", "message"),
    [
        ("/dev/ttyACM0", "00037376", ENABLE, "stable /dev/serial/by-id"),
        (DEVICE, "wrong", ENABLE, "controller serial"),
        (DEVICE, "00037376", "wrong-token", "enable token"),
    ],
)
def test_open_fails_closed_before_transport_creation(
    manifest: HardwareManifest,
    path: str,
    serial: str,
    token: str,
    message: str,
) -> None:
    calls = 0

    def factory(_path: str, _timeout: float) -> SerialTransport:
        nonlocal calls
        calls += 1
        return cast(SerialTransport, FakeSerial())

    instance = MaestroAdapter(
        manifest=manifest,
        stable_device_path=path,
        expected_controller_serial=serial,
        required_enable_token=ENABLE,
        clock=lambda: 1_500,
        transport_factory=factory,
    )

    with pytest.raises(MaestroConnectionError, match=message):
        instance.open(token)
    assert calls == 0


def test_apply_writes_exact_command_confirms_position_and_checks_errors(
    manifest: HardwareManifest,
) -> None:
    target = manifest.actuator("mouth_open").home_qus
    fake = FakeSerial(reads=(target.to_bytes(2, "little"), b"\x00\x00"))
    instance = adapter(manifest, fake)
    instance.open(ENABLE)

    status = instance.apply(authorized(request(manifest)))

    assert status.state is ActuatorStatusState.APPLIED
    assert status.applied_targets == request(manifest).targets
    assert bytes(fake.written) == (
        encode_set_target(6, target) + encode_get_position(6) + encode_get_errors()
    )


def test_write_all_handles_partial_writes(manifest: HardwareManifest) -> None:
    target = manifest.actuator("mouth_open").home_qus
    fake = FakeSerial(
        reads=(target.to_bytes(2, "little"), b"\x00\x00"),
        write_sizes=(2, 2, 1, 1, 1),
    )
    instance = adapter(manifest, fake)
    instance.open(ENABLE)

    status = instance.apply(authorized(request(manifest)))
    assert status.state is ActuatorStatusState.APPLIED


def test_timeout_returns_fault_without_claiming_current_target_applied(
    manifest: HardwareManifest,
) -> None:
    fake = FakeSerial(reads=())
    instance = adapter(manifest, fake)
    instance.open(ENABLE)

    status = instance.apply(authorized(request(manifest)))

    assert status.state is ActuatorStatusState.FAULT
    assert status.fault_code == "serial-read-timeout"
    assert status.applied_targets == ()
    assert "physical state unknown" in (status.detail or "")


def test_short_reads_are_reassembled(manifest: HardwareManifest) -> None:
    target = manifest.actuator("mouth_open").home_qus
    encoded = target.to_bytes(2, "little")
    fake = FakeSerial(reads=(encoded[:1], encoded[1:], b"\x00", b"\x00"))
    instance = adapter(manifest, fake)
    instance.open(ENABLE)

    status = instance.apply(authorized(request(manifest)))
    assert status.state is ActuatorStatusState.APPLIED


def test_error_register_returns_fault_with_confirmed_application(
    manifest: HardwareManifest,
) -> None:
    target = manifest.actuator("mouth_open").home_qus
    fake = FakeSerial(reads=(target.to_bytes(2, "little"), b"\x04\x00"))
    instance = adapter(manifest, fake)
    instance.open(ENABLE)

    status = instance.apply(authorized(request(manifest)))

    assert status.state is ActuatorStatusState.FAULT
    assert status.fault_code == "maestro-error-register-0x0004"
    assert status.applied_targets == request(manifest).targets


def test_second_target_failure_preserves_only_first_confirmed_target(
    manifest: HardwareManifest,
) -> None:
    first = manifest.actuator("mouth_open")
    second = manifest.actuator("forehead_frown")
    proposed = request(
        manifest,
        targets=(
            {"actuator_name": first.name, "normalized_position": 0.0},
            {"actuator_name": second.name, "normalized_position": 0.0},
        ),
    )
    fake = FakeSerial(reads=(first.home_qus.to_bytes(2, "little"),))
    instance = adapter(manifest, fake)
    instance.open(ENABLE)

    status = instance.apply(authorized(proposed))

    assert status.state is ActuatorStatusState.FAULT
    assert status.applied_targets == proposed.targets[:1]
    assert second.name in (status.detail or "")
    assert "physical state unknown" in (status.detail or "")


def test_apply_rejects_raw_and_mismatched_authority_before_writes(
    manifest: HardwareManifest,
) -> None:
    fake = FakeSerial()
    instance = adapter(manifest, fake)
    instance.open(ENABLE)
    proposed = request(manifest)
    mismatched = proposed.model_copy(update={"calibration_sha256": "b" * 64})

    with pytest.raises(ActuatorAuthorizationError, match="authorization wrapper"):
        instance.apply(proposed)  # type: ignore[arg-type]
    with pytest.raises(ActuatorAuthorizationError, match="calibration_sha256 mismatch"):
        instance.apply(authorized(mismatched))
    assert fake.written == b""


def test_close_is_idempotent_and_apply_requires_open(
    manifest: HardwareManifest,
) -> None:
    fake = FakeSerial()
    instance = adapter(manifest, fake)

    with pytest.raises(MaestroConnectionError, match="not open"):
        instance.apply(authorized(request(manifest)))
    instance.open(ENABLE)
    instance.close()
    instance.close()

    assert fake.closed is True
    assert instance.is_open is False
