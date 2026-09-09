"""Disconnected tests for the guarded Maestro serial adapter."""

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
import serial

from alice.contracts.actuation import ActuatorStatusState, PoseRequest
from alice.hardware.adapter import ActuatorAuthorizationError, AdapterMode
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
from alice.safety.permits import ActuationPermit, PermitKind
from alice.safety.supervisor import AuthorizationDecision, RunState

MANIFEST_PATH = Path(__file__).parents[2] / "hardware" / "alice-face-v1.yaml"
DEVICE = (
    "/dev/serial/by-id/usb-Pololu_Corporation_"
    "Pololu_Mini_Maestro_12-Channel_USB_Servo_Controller_00037376-if00"
)
OTHER_INTERFACE = (
    "/dev/serial/by-id/usb-Pololu_Corporation_"
    "Pololu_Mini_Maestro_12-Channel_USB_Servo_Controller_00037376-if02"
)
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

    def fileno(self) -> int:
        return 37


class ReadErrorSerial(FakeSerial):
    def read(self, size: int) -> bytes:
        raise OSError("synthetic read failure")


class OverflowSerial(FakeSerial):
    def read(self, size: int) -> bytes:
        return b"\x00" * (size + 1)


class CloseErrorSerial(FakeSerial):
    def close(self) -> None:
        self.closed = True
        raise OSError("synthetic close failure")


@dataclass
class PollClock:
    now_ns: int = 1_500
    sleeps: list[float] = field(default_factory=list)

    def __call__(self) -> int:
        return self.now_ns

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now_ns += round(seconds * 1_000_000_000)


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
        permit_verifier=PermissiveVerifier(),
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
        permit_verifier=PermissiveVerifier(),
    )

    assert calls == 0
    assert instance.is_open is False


def test_raw_transport_fd_requires_an_open_transport_and_valid_integer(
    manifest: HardwareManifest,
) -> None:
    fake = FakeSerial()
    instance = adapter(manifest, fake)
    with pytest.raises(MaestroConnectionError, match="not open"):
        instance.fileno()

    instance.open(ENABLE)
    assert instance.fileno() == 37

    fake.fileno = lambda: -1  # type: ignore[method-assign]
    with pytest.raises(MaestroConnectionError, match="nonnegative integer"):
        instance.fileno()


@pytest.mark.parametrize(
    ("path", "serial", "token", "message"),
    [
        ("/dev/ttyACM0", "00037376", ENABLE, "stable /dev/serial/by-id"),
        (OTHER_INTERFACE, "00037376", ENABLE, "exactly match"),
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
        permit_verifier=PermissiveVerifier(),
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


def test_read_only_preflight_reads_errors_and_positions_without_set_target(
    manifest: HardwareManifest,
) -> None:
    names = ("mouth_open",)
    home = manifest.actuator("mouth_open").home_qus
    fake = FakeSerial(reads=(b"\x00\x00", home.to_bytes(2, "little")))
    instance = adapter(manifest, fake)
    instance.open(ENABLE)

    snapshot = instance.read_only_preflight(names)

    assert snapshot.controller_error_register == 0
    assert snapshot.positions_qus == {"mouth_open": home}
    assert bytes(fake.written) == encode_get_errors() + encode_get_position(6)
    assert b"\x84" not in bytes(fake.written)


def test_partial_write_poisons_transport_and_prevents_reuse(
    manifest: HardwareManifest,
) -> None:
    target = manifest.actuator("mouth_open").home_qus
    fake = FakeSerial(
        reads=(target.to_bytes(2, "little"), b"\x00\x00"),
        write_sizes=(2, 2, 1, 1, 1),
    )
    instance = adapter(manifest, fake)
    instance.open(ENABLE)

    status = instance.apply(authorized(request(manifest)))
    assert status.state is ActuatorStatusState.FAULT
    assert status.fault_code == "serial-partial-write"
    assert status.applied_targets == ()
    assert fake.closed is True
    assert instance.is_open is False
    assert instance.is_poisoned is True
    with pytest.raises(MaestroConnectionError, match="poisoned"):
        instance.open(ENABLE)


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
    assert "controller command state is unknown" in (status.detail or "")
    assert fake.closed is True
    assert instance.is_poisoned is True


def test_short_reads_are_reassembled(manifest: HardwareManifest) -> None:
    target = manifest.actuator("mouth_open").home_qus
    encoded = target.to_bytes(2, "little")
    fake = FakeSerial(reads=(encoded[:1], encoded[1:], b"\x00", b"\x00"))
    instance = adapter(manifest, fake)
    instance.open(ENABLE)

    status = instance.apply(authorized(request(manifest)))
    assert status.state is ActuatorStatusState.APPLIED


def test_intermediate_controller_positions_are_polled_until_target(
    manifest: HardwareManifest,
) -> None:
    target = manifest.actuator("mouth_open").home_qus
    fake = FakeSerial(
        reads=(
            (target - 100).to_bytes(2, "little"),
            (target - 10).to_bytes(2, "little"),
            target.to_bytes(2, "little"),
            b"\x00\x00",
        )
    )
    clock = PollClock()
    instance = MaestroAdapter(
        manifest=manifest,
        stable_device_path=DEVICE,
        expected_controller_serial="00037376",
        required_enable_token=ENABLE,
        clock=clock,
        permit_verifier=PermissiveVerifier(),
        transport_factory=lambda _path, _timeout: cast(SerialTransport, fake),
        settle_timeout_ns=10_000_000,
        poll_interval_ns=1_000_000,
        sleeper=clock.sleep,
    )
    instance.open(ENABLE)

    status = instance.apply(authorized(request(manifest)))

    assert status.state is ActuatorStatusState.APPLIED
    assert bytes(fake.written).count(encode_get_position(6)) == 3
    assert clock.sleeps == [0.001, 0.001]


def test_position_settle_timeout_poisons_transport(manifest: HardwareManifest) -> None:
    target = manifest.actuator("mouth_open").home_qus
    fake = FakeSerial(reads=((target - 1).to_bytes(2, "little"),) * 3)
    clock = PollClock()
    instance = MaestroAdapter(
        manifest=manifest,
        stable_device_path=DEVICE,
        expected_controller_serial="00037376",
        required_enable_token=ENABLE,
        clock=clock,
        permit_verifier=PermissiveVerifier(),
        transport_factory=lambda _path, _timeout: cast(SerialTransport, fake),
        settle_timeout_ns=2_000_000,
        poll_interval_ns=1_000_000,
        sleeper=clock.sleep,
    )
    instance.open(ENABLE)

    status = instance.apply(authorized(request(manifest)))

    assert status.fault_code == "position-settle-timeout"
    assert status.applied_targets == ()
    assert fake.closed is True
    assert instance.is_poisoned is True
    assert clock.sleeps == [0.001, 0.001]


@pytest.mark.parametrize(
    ("fake", "fault_code"),
    [
        (ReadErrorSerial(), "serial-read-error"),
        (OverflowSerial(), "serial-read-overflow"),
        (CloseErrorSerial(), "serial-read-timeout"),
    ],
)
def test_read_transaction_faults_poison_even_if_cleanup_fails(
    manifest: HardwareManifest,
    fake: FakeSerial,
    fault_code: str,
) -> None:
    instance = adapter(manifest, fake)
    instance.open(ENABLE)

    status = instance.apply(authorized(request(manifest)))

    assert status.fault_code == fault_code
    assert fake.closed is True
    assert instance.is_poisoned is True
    assert instance.is_open is False


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
    assert status.controller_output_samples
    assert status.targets_reached is True


def test_maestro_identity_is_immutable_hardware_attestation(
    manifest: HardwareManifest,
) -> None:
    fake = FakeSerial()
    instance = adapter(manifest, fake)

    assert instance.identity.backend == "maestro"
    assert instance.identity.mode is AdapterMode.HARDWARE
    assert instance.identity.hardware_capable is True
    with pytest.raises(Exception):
        instance.identity.backend = "mock"  # type: ignore[misc]


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
    assert "controller command state is unknown" in (status.detail or "")


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


def test_disabled_jaw_initialization_writes_only_its_calibrated_home(manifest):
    from alice.experiments.jaw_trial_cli import scoped_manifest

    home = (5059).to_bytes(2, "little")
    fake = FakeSerial(reads=(b"\0\0", b"\0\0", home, b"\0\0", home))
    instance = adapter(scoped_manifest(manifest), fake)
    instance.open(ENABLE)
    snapshot = instance.initialize_disabled_jaw_home(ENABLE)
    assert snapshot.positions_qus == {"mouth_open": 5059}
    assert bytes(fake.written) == (
        encode_get_errors()
        + encode_get_position(6)
        + encode_set_target(6, 5059)
        + encode_get_position(6)
        + encode_get_errors()
        + encode_get_position(6)
    )


@pytest.mark.parametrize(
    "case", ["full-manifest", "wrong-token", "nonzero", "error", "short-read"]
)
def test_jaw_initialization_rejects_invalid_scope_state_or_token(manifest, case):
    from alice.experiments.jaw_trial_cli import scoped_manifest

    document = manifest if case == "full-manifest" else scoped_manifest(manifest)
    reads = (
        ()
        if case == "short-read"
        else (
            (1 if case == "error" else 0).to_bytes(2, "little"),
            (5000 if case == "nonzero" else 0).to_bytes(2, "little"),
        )
    )
    fake = FakeSerial(reads=reads)
    instance = adapter(document, fake)
    instance.open(ENABLE)
    with pytest.raises(MaestroConnectionError):
        instance.initialize_disabled_jaw_home(
            "wrong" if case == "wrong-token" else ENABLE
        )
    assert encode_set_target(6, 5059) not in bytes(fake.written)


def test_jaw_initialization_fault_after_write_poisons_transport(manifest):
    from alice.experiments.jaw_trial_cli import scoped_manifest

    home = (5059).to_bytes(2, "little")
    fake = FakeSerial(reads=(b"\0\0", b"\0\0", home, b"\1\0", home))
    instance = adapter(scoped_manifest(manifest), fake)
    instance.open(ENABLE)
    with pytest.raises(MaestroConnectionError, match="error"):
        instance.initialize_disabled_jaw_home(ENABLE)
    assert instance.is_poisoned
    assert fake.closed


def test_jaw_startup_can_observe_disabled_pwm_until_first_controller_update(manifest):
    from alice.experiments.jaw_trial_cli import scoped_manifest

    home = (5059).to_bytes(2, "little")
    fake = FakeSerial(reads=(b"\0\0", b"\0\0", b"\0\0", home, b"\0\0", home))
    instance = adapter(scoped_manifest(manifest), fake)
    instance.open(ENABLE)
    snapshot = instance.initialize_disabled_jaw_home(ENABLE)
    assert snapshot.positions_qus["mouth_open"] == 5059
    assert bytes(fake.written).count(encode_set_target(6, 5059)) == 1


def test_jaw_initialization_cancel_poisons_transport(manifest, monkeypatch):
    from alice.experiments.jaw_trial_cli import scoped_manifest

    home = (5059).to_bytes(2, "little")
    fake = FakeSerial(reads=(b"\0\0", b"\0\0", home))
    instance = adapter(scoped_manifest(manifest), fake)
    instance.open(ENABLE)

    def interrupt(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(instance, "_sleeper", interrupt)
    with pytest.raises(KeyboardInterrupt):
        instance.initialize_disabled_jaw_home(ENABLE)
    assert instance.is_poisoned
    assert fake.closed


def test_streaming_jaw_send_records_current_output_without_waiting_for_target(manifest):
    from alice.experiments.jaw_trial_cli import scoped_manifest

    scope = scoped_manifest(manifest)
    fake = FakeSerial(reads=(b"\0\0", (5059).to_bytes(2, "little")))
    instance = adapter(scope, fake)
    instance.open(ENABLE)
    command = request(
        scope, targets=({"actuator_name": "mouth_open", "normalized_position": 1.0},)
    )
    receipt = instance.stream_jaw_target(ENABLE, command)
    assert receipt.target_qus == 5440
    assert receipt.observed_qus == 5059
    assert receipt.state == "sent"
    assert bytes(fake.written) == encode_set_target(
        6, 5440
    ) + encode_get_errors() + encode_get_position(6)


def test_streaming_jaw_cannot_write_another_axis(manifest):
    fake = FakeSerial()
    instance = adapter(manifest, fake)
    instance.open(ENABLE)
    with pytest.raises(MaestroConnectionError):
        instance.stream_jaw_target(
            ENABLE,
            request(
                manifest,
                targets=({"actuator_name": "head_tilt", "normalized_position": 0.1},),
            ),
        )
    assert not fake.written


@pytest.mark.parametrize("observed,errors", [(0, 0), (5059, 1), (6000, 0)])
def test_streaming_jaw_controller_fault_closes_transport(manifest, observed, errors):
    from alice.experiments.jaw_trial_cli import scoped_manifest

    scope = scoped_manifest(manifest)
    fake = FakeSerial(
        reads=(errors.to_bytes(2, "little"), observed.to_bytes(2, "little"))
    )
    instance = adapter(scope, fake)
    instance.open(ENABLE)
    with pytest.raises(MaestroConnectionError):
        instance.stream_jaw_target(ENABLE, request(scope))
    assert instance.is_poisoned
    assert fake.closed


def test_stream_rejects_late_dispatch_before_writing(manifest):
    from alice.experiments.jaw_trial_cli import scoped_manifest

    scope = scoped_manifest(manifest)
    fake = FakeSerial()
    instance = adapter(scope, fake)
    instance.open(ENABLE)
    command = request(scope).model_copy(
        update={"issued_monotonic_ns": 0, "expires_monotonic_ns": 500_000_000}
    )
    instance._clock = lambda: 3_000_000
    with pytest.raises(MaestroConnectionError, match="dispatch"):
        instance.stream_jaw_target(ENABLE, command)
    assert not fake.written


def test_stream_send_timestamp_excludes_readback_latency(manifest):
    from alice.experiments.jaw_trial_cli import scoped_manifest

    scope = scoped_manifest(manifest)
    now = [1500]

    class SlowRead(FakeSerial):
        def read(self, size):
            now[0] += 20_000_000
            return super().read(size)

    fake = SlowRead(reads=(b"\0\0", (5059).to_bytes(2, "little")))
    instance = adapter(scope, fake)
    instance._clock = lambda: now[0]
    instance.open(ENABLE)
    receipt = instance.stream_jaw_target(ENABLE, request(scope))
    assert receipt.sent_monotonic_ns == 1500
    assert receipt.reported_monotonic_ns == 40_001_500


def test_fast_jaw_dynamics_only_changes_channel_six_and_restores_explicitly(manifest):
    from alice.experiments.jaw_trial_cli import scoped_manifest

    fake = FakeSerial(reads=(b"\0\0", (5059).to_bytes(2, "little"), b"\0\0", b"\0\0"))
    instance = adapter(scoped_manifest(manifest), fake)
    instance.open(ENABLE)
    instance.enable_fast_jaw_response(ENABLE)
    instance.restore_jaw_response()
    instance.close()
    assert bytes(fake.written) == bytes(
        [
            0xA1,
            0x90,
            6,  # errors and Home before changing dynamics
            0x87,
            6,
            0,
            0,
            0x89,
            6,
            0,
            0,
            0xA1,  # fastest runtime response
            0x87,
            6,
            0,
            0,
            0x89,
            6,
            11,
            0,
            0xA1,  # restore stored profile
        ]
    )
    assert instance.jaw_response_override["status"] == "restored"


@pytest.mark.parametrize(
    "wrong_scope,wrong_token,position",
    [(True, False, 5059), (False, True, 5059), (False, False, 5440)],
)
def test_fast_jaw_dynamics_rejects_wrong_scope_token_or_nonhome(
    manifest, wrong_scope, wrong_token, position
):
    from alice.experiments.jaw_trial_cli import scoped_manifest

    fake = FakeSerial(reads=(b"\0\0", position.to_bytes(2, "little")))
    instance = adapter(manifest if wrong_scope else scoped_manifest(manifest), fake)
    instance.open(ENABLE)
    with pytest.raises(MaestroConnectionError):
        instance.enable_fast_jaw_response("wrong" if wrong_token else ENABLE)
    assert b"\x87" not in fake.written and b"\x89" not in fake.written


def test_fast_jaw_dynamics_marks_failed_restoration_and_closes(manifest):
    from alice.experiments.jaw_trial_cli import scoped_manifest

    fake = FakeSerial(reads=(b"\0\0", (5059).to_bytes(2, "little"), b"\0\0"))
    instance = adapter(scoped_manifest(manifest), fake)
    instance.open(ENABLE)
    instance.enable_fast_jaw_response(ENABLE)
    with pytest.raises(Exception, match="read"):
        instance.restore_jaw_response()
    assert fake.closed
    assert instance.jaw_response_override["status"] == "restoration-failed"


def test_watchdog_close_during_stream_read_sends_no_restoration_commands(manifest):
    import threading

    from alice.experiments.jaw_trial_cli import scoped_manifest

    entered, release = threading.Event(), threading.Event()
    failures = []

    class BlockingRead(FakeSerial):
        block = False

        def read(self, size):
            if self.block and threading.current_thread().name == "serial-owner":
                entered.set()
                assert release.wait(2)
            return super().read(size)

    scope = scoped_manifest(manifest)
    fake = BlockingRead(
        reads=(
            b"\0\0",
            (5059).to_bytes(2, "little"),
            b"\0\0",
            b"\0\0",
            (5059).to_bytes(2, "little"),
        )
    )
    instance = adapter(scope, fake)
    instance.open(ENABLE)
    instance.enable_fast_jaw_response(ENABLE)
    fake.block = True

    def send():
        try:
            instance.stream_jaw_target(ENABLE, request(scope))
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=send, name="serial-owner")
    worker.start()
    try:
        assert entered.wait(2)
        before_close = bytes(fake.written)
        instance.close()
        assert bytes(fake.written) == before_close
        assert instance.jaw_response_override["status"] == "not-restored-close"
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert fake.closed
