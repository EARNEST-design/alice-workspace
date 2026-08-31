from __future__ import annotations

import copy
import gc
import hashlib
import inspect
import json
import os
import pickle
import time
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr, ValidationError

import alice.experiments.hardware_identification as module
from alice.experiments.hardware_identification import (
    ElectricalSafetyEvidence,
    HardwareApproval,
    HardwareIdentificationConfig,
    HardwarePreflightAttestation,
    LinuxUsbIdentity,
    PowerEnableConfirmation,
    PreparedHardwareHandle,
    cancel_prepared_hardware_identification,
    execute_prepared_hardware_identification,
    load_hardware_identification_config,
    prepare_hardware_identification,
)
from alice.hardware.adapter import AdapterIdentity, AdapterMode
from alice.hardware.maestro_adapter import MaestroPreflightSnapshot

ROOT = Path(__file__).parents[2]
CONFIG_TEMPLATE = ROOT / "config/experiments/actuator-identification-hardware.yaml"
MANIFEST_PATH = ROOT / "hardware/alice-face-v1.yaml"


class RecordingMaestro:
    constructed: list[dict[str, object]] = []
    instances: list[RecordingMaestro] = []

    def __init__(self, **kwargs: object) -> None:
        self.constructed.append(kwargs)
        self.instances.append(self)
        self.writes = 0
        self.closed = False
        self.raw_fd, self._pipe_writer = os.pipe()

    @property
    def identity(self) -> AdapterIdentity:
        return AdapterIdentity(
            backend="maestro", mode=AdapterMode.HARDWARE, hardware_capable=True
        )

    def open(self, token: str) -> None:
        assert token == "run-secret-not-in-repository"

    def read_only_preflight(self, names: tuple[str, ...]) -> MaestroPreflightSnapshot:
        assert names == ("mouth_open",)
        return MaestroPreflightSnapshot(
            controller_error_register=0,
            positions_qus={"mouth_open": 5059},
            observed_monotonic_ns=time.monotonic_ns(),
        )

    def apply(self, authorization: object) -> object:
        self.writes += 1
        raise AssertionError("test must not actuate")

    def fileno(self) -> int:
        return self.raw_fd

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        os.close(self.raw_fd)
        os.close(self._pipe_writer)


def _fd_is_closed(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return True
    return False


def _make_files(tmp_path: Path) -> tuple[Path, Path, ElectricalSafetyEvidence]:
    evidence_doc = tmp_path / "review.pdf"
    evidence_doc.write_bytes(b"reviewed electrical evidence")
    evidence = ElectricalSafetyEvidence(
        schema_version="electrical-safety-evidence/v1",
        evidence_id="electrical-review-20260901",
        source=str(evidence_doc),
        source_document_sha256=hashlib.sha256(evidence_doc.read_bytes()).hexdigest(),
        reviewed_at=datetime.now(UTC),
        reviewer="operator-review-001",
        supply_voltage_v=6.0,
        current_limit_a=5.0,
        scope="Alice face servo rail; one channel at conservative offset",
    )
    evidence_path = tmp_path / "electrical.yaml"
    evidence_path.write_text(yaml.safe_dump(evidence.model_dump(mode="json")))
    evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    config_path = tmp_path / "hardware.yaml"
    config_path.write_text(
        CONFIG_TEMPLATE.read_text()
        .replace("REQUIRED_RUN_SPECIFIC_RUN_ID", "hardware-run-20260901-001")
        .replace("REQUIRED_RUN_SPECIFIC_APPROVAL_ID", "review-20260901-operator")
        .replace(
            "REQUIRED_RUN_SPECIFIC_ENABLE_TOKEN", "run-secret-not-in-repository"
        )
        .replace("REQUIRED_REVIEWED_ELECTRICAL_EVIDENCE_PATH", str(evidence_path))
        .replace("REQUIRED_ELECTRICAL_EVIDENCE_SHA256", evidence_hash)
    )
    return config_path, evidence_path, evidence


def _attestation(config: HardwareIdentificationConfig) -> HardwarePreflightAttestation:
    return HardwarePreflightAttestation(
        run_id=config.run_id,
        observed_at=datetime.now(UTC),
        observed_monotonic_ns=time.monotonic_ns(),
        source="operator at Alice",
        operator_acknowledgment="I CONFIRM PREFLIGHT WITH MASTER SERVO POWER OFF",
        master_servo_power_removed=True,
        emergency_power_removal_ready=True,
        mechanical_clearance_verified=True,
        channel_10_linkage_verified=True,
        command_interface_role_verified=True,
        no_competing_processes=True,
        phase_1_camera_accepted=True,
    )


def _approval(
    config_path: Path, config: HardwareIdentificationConfig
) -> HardwareApproval:
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    manifest_hash = hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()
    return HardwareApproval(
        approval_id=config.approval_id,
        enable_token="run-secret-not-in-repository",
        run_id=config.run_id,
        config_sha256=config_hash,
        manifest_sha256=manifest_hash,
        electrical_evidence_sha256=config.electrical_evidence_sha256,
        approved_at=datetime.now(UTC),
        approved_monotonic_ns=time.monotonic_ns(),
        source="operator procedure review",
        operator_acknowledgment="I APPROVE READ-ONLY PREFLIGHT WITH SERVO POWER OFF",
        confirmation_text=(
            f"PREPARE {config.run_id} CONFIG {config_hash} MANIFEST {manifest_hash} "
            f"ELECTRICAL {config.electrical_evidence_sha256}"
        ),
    )


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    RecordingMaestro.constructed.clear()
    RecordingMaestro.instances.clear()
    config_path, _, _ = _make_files(tmp_path)
    config = load_hardware_identification_config(config_path)
    monkeypatch.setattr(module, "MaestroAdapter", RecordingMaestro)
    monkeypatch.setattr(
        module,
        "_resolve_linux_usb_identity",
        lambda _: LinuxUsbIdentity(
            serial_number="00037376",
            interface_number="00",
            resolved_tty="/dev/ttyACM0",
        ),
    )
    attestation = _attestation(config)
    approval = _approval(config_path, config)
    result = prepare_hardware_identification(
        config_path=config_path,
        manifest_path=MANIFEST_PATH,
        approval=approval,
        attestation=attestation,
        enable_hardware=True,
    )
    yield result, config_path, config
    try:
        cancel_prepared_hardware_identification(result)
    except ValueError:
        pass


def test_public_lifecycle_has_two_stages_and_no_combined_runner() -> None:
    assert not hasattr(module, "run_hardware_identification")
    for function in (
        prepare_hardware_identification,
        execute_prepared_hardware_identification,
    ):
        parameters = inspect.signature(function).parameters
        assert not {
            "adapter",
            "adapter_factory",
            "transport",
            "transport_factory",
            "supervisor",
            "clock",
            "sleeper",
            "resolver",
        } & parameters.keys()
    assert [item.name for item in fields(PreparedHardwareHandle)] == [
        "challenge",
        "issuance_token",
    ]


def test_forged_or_wrong_process_handle_is_rejected_without_io(tmp_path: Path) -> None:
    challenge = module.PowerEnableChallenge(
        challenge_id="forged",
        run_id="forged-run",
        config_sha256="a" * 64,
        manifest_sha256="b" * 64,
        electrical_evidence_sha256="c" * 64,
        issued_at=datetime.now(UTC),
        issued_monotonic_ns=time.monotonic_ns(),
        expires_monotonic_ns=time.monotonic_ns() + 1_000_000_000,
        challenge_sha256="d" * 64,
    )
    forged = PreparedHardwareHandle(
        challenge=challenge,
        issuance_token=SecretStr("token-from-another-process"),
    )
    with pytest.raises(ValueError, match="unknown"):
        execute_prepared_hardware_identification(
            prepared=forged,
            observer=object(),
            output_dir=tmp_path / "unused",
            confirmation=_power_confirmation(challenge),
        )
    with pytest.raises(ValueError, match="unknown"):
        cancel_prepared_hardware_identification(forged)


def test_challenge_is_frozen_and_mutated_copy_burns_prepared_authority(
    prepared, tmp_path: Path
) -> None:
    result, _, _ = prepared
    with pytest.raises(ValidationError):
        result.challenge.run_id = "mutated"  # type: ignore[misc]
    copied = replace(
        result,
        challenge=result.challenge.model_copy(update={"run_id": "mutated"}),
    )
    with pytest.raises(ValueError, match="authority"):
        execute_prepared_hardware_identification(
            prepared=copied,
            observer=object(),
            output_dir=tmp_path / "unused",
            confirmation=_power_confirmation(copied.challenge),
        )
    assert RecordingMaestro.instances[-1].closed is True
    with pytest.raises(ValueError, match="unknown|consumed"):
        cancel_prepared_hardware_identification(result)


def test_copied_capability_is_unusable_and_burns_original(prepared) -> None:
    result, _, _ = prepared
    copied = PreparedHardwareHandle(
        challenge=result.challenge,
        issuance_token=SecretStr(result.issuance_token.get_secret_value()),
    )

    with pytest.raises(ValueError, match="authority"):
        cancel_prepared_hardware_identification(copied)
    assert RecordingMaestro.instances[-1].closed is True
    with pytest.raises(ValueError, match="unknown|consumed"):
        cancel_prepared_hardware_identification(result)


def test_prepared_capability_disallows_copy_and_serialization(prepared) -> None:
    result, _, _ = prepared
    for operation in (
        lambda: copy.copy(result),
        lambda: copy.deepcopy(result),
        lambda: pickle.dumps(result),
    ):
        with pytest.raises(TypeError, match="cannot be (copied|serialized)"):
            operation()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_fork_detaches_child_authority_and_parent_remains_valid(prepared) -> None:
    result, _, _ = prepared
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            rejected = False
            try:
                cancel_prepared_hardware_identification(result)
            except ValueError:
                rejected = True
            payload = json.dumps(
                {
                    "rejected": rejected,
                    "raw_fd_closed": _fd_is_closed(
                        RecordingMaestro.instances[-1].raw_fd
                    ),
                }
            ).encode()
            os.write(write_fd, payload)
        finally:
            os.close(write_fd)
            os._exit(0)

    os.close(write_fd)
    payload = os.read(read_fd, 4096)
    os.close(read_fd)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    child = json.loads(payload)
    assert child == {"rejected": True, "raw_fd_closed": True}
    assert RecordingMaestro.instances[-1].closed is False
    cancel_prepared_hardware_identification(result)
    assert RecordingMaestro.instances[-1].closed is True


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_fork_child_cleanup_ignores_held_python_cleanup_locks(prepared) -> None:
    result, _, _ = prepared
    adapter = RecordingMaestro.instances[-1]
    adapter._cleanup_lock = module.threading.Lock()
    adapter._cleanup_lock.acquire()
    module._registry_lock.acquire()
    read_fd, write_fd = os.pipe()
    try:
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            rejected = False
            raw_fd_closed = False
            try:
                cancel_prepared_hardware_identification(result)
            except ValueError:
                rejected = True
            try:
                os.fstat(adapter.raw_fd)
            except OSError:
                raw_fd_closed = True
            os.write(
                write_fd,
                json.dumps(
                    {"rejected": rejected, "raw_fd_closed": raw_fd_closed}
                ).encode(),
            )
            os._exit(0)
    finally:
        module._registry_lock.release()
        adapter._cleanup_lock.release()
        os.close(write_fd)
    payload = os.read(read_fd, 4096)
    os.close(read_fd)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert json.loads(payload) == {"rejected": True, "raw_fd_closed": True}
    cancel_prepared_hardware_identification(result)


def test_prepare_fails_before_issuing_capability_without_raw_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _, _ = _make_files(tmp_path)
    config = load_hardware_identification_config(config_path)
    monkeypatch.setattr(module, "MaestroAdapter", RecordingMaestro)
    monkeypatch.setattr(RecordingMaestro, "fileno", lambda self: -1)
    monkeypatch.setattr(
        module,
        "_resolve_linux_usb_identity",
        lambda _: LinuxUsbIdentity(
            serial_number="00037376",
            interface_number="00",
            resolved_tty="/dev/ttyACM0",
        ),
    )
    attestation = _attestation(config)
    approval = _approval(config_path, config)
    with pytest.raises(RuntimeError, match="raw OS file descriptor"):
        prepare_hardware_identification(
            config_path=config_path,
            manifest_path=MANIFEST_PATH,
            approval=approval,
            attestation=attestation,
            enable_hardware=True,
        )
    assert module._prepared_registry == {}
    assert RecordingMaestro.instances[-1].closed is True


def test_prepare_returns_zero_motion_challenge(prepared) -> None:
    result, _, _ = prepared
    adapter = RecordingMaestro.instances[-1]
    assert result.challenge.run_id == "hardware-run-20260901-001"
    assert result.challenge.expires_monotonic_ns > time.monotonic_ns()
    assert adapter.writes == 0
    assert adapter.closed is False
    assert RecordingMaestro.constructed[-1]["stable_device_path"].endswith(
        "00037376-if00"
    )


def test_token_collision_retry_binds_handle_to_registered_token(
    prepared, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, config_path, config = prepared
    first_token = first.issuance_token.get_secret_value()
    candidates = iter(
        ("second-power-challenge", first_token, "fresh-collision-free-token")
    )
    monkeypatch.setattr(module.secrets, "token_urlsafe", lambda _: next(candidates))
    attestation = _attestation(config)
    approval = _approval(config_path, config)
    second = prepare_hardware_identification(
        config_path=config_path,
        manifest_path=MANIFEST_PATH,
        approval=approval,
        attestation=attestation,
        enable_hardware=True,
    )
    assert second.issuance_token.get_secret_value() == "fresh-collision-free-token"
    cancel_prepared_hardware_identification(second)


def _power_confirmation(challenge) -> PowerEnableConfirmation:
    return PowerEnableConfirmation(
        run_id=challenge.run_id,
        challenge_id=challenge.challenge_id,
        config_sha256=challenge.config_sha256,
        manifest_sha256=challenge.manifest_sha256,
        electrical_evidence_sha256=challenge.electrical_evidence_sha256,
        challenge_sha256=challenge.challenge_sha256,
        confirmed_at=datetime.now(UTC),
        confirmed_monotonic_ns=time.monotonic_ns(),
        source="operator at master servo switch",
        operator_acknowledgment=(
            "I CONFIRM MASTER SERVO POWER IS ON AND POWER REMOVAL IS READY"
        ),
    )


def test_execution_requires_new_confirmation_and_is_single_use(
    prepared, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _, _ = prepared
    called = False
    captured: dict[str, object] = {}

    def fake_core(**kwargs: object) -> object:
        nonlocal called
        called = True
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(module, "_run_identification_core", fake_core)
    execute_prepared_hardware_identification(
        prepared=result,
        observer=object(),
        output_dir=tmp_path / "run",
        confirmation=_power_confirmation(result.challenge),
    )
    assert called is True
    stored_config = captured["config"].model_dump(mode="json")
    assert "enable_token" not in stored_config
    assert "run-secret-not-in-repository" not in json.dumps(stored_config)
    provenance = captured["hardware_provenance"]
    assert (
        provenance.approved_raw_config_sha256 == result.challenge.config_sha256
    )
    execution_hash = hashlib.sha256(
        json.dumps(stored_config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert provenance.execution_config_sha256 == execution_hash
    assert provenance.usb_identity.interface_number == "00"
    assert provenance.read_only_preflight.issued_set_target is False
    assert provenance.independent_watchdog.survives_process_death is False
    serialized_provenance = provenance.model_dump_json()
    assert "run-secret-not-in-repository" not in serialized_provenance
    assert "enable_token" not in serialized_provenance
    assert "confirmation_text" not in serialized_provenance
    assert result.issuance_token.get_secret_value() not in serialized_provenance
    with pytest.raises(ValueError, match="consumed"):
        execute_prepared_hardware_identification(
            prepared=result,
            observer=object(),
            output_dir=tmp_path / "again",
            confirmation=_power_confirmation(result.challenge),
        )


def test_pre_prepare_power_confirmation_is_rejected(prepared, tmp_path: Path) -> None:
    result, _, _ = prepared
    confirmation = _power_confirmation(result.challenge).model_copy(
        update={"confirmed_monotonic_ns": result.challenge.issued_monotonic_ns - 1}
    )
    with pytest.raises(ValueError, match="after preparation"):
        execute_prepared_hardware_identification(
            prepared=result,
            observer=object(),
            output_dir=tmp_path / "run",
            confirmation=confirmation,
        )
    assert RecordingMaestro.instances[-1].closed is True


def test_mutation_after_prepare_does_not_change_execution(
    prepared, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, config_path, _ = prepared
    config_path.write_text("corrupted after prepare")
    captured: dict[str, object] = {}

    def retained_core(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(module, "_run_identification_core", retained_core)
    execute_prepared_hardware_identification(
        prepared=result,
        observer=object(),
        output_dir=tmp_path / "run",
        confirmation=_power_confirmation(result.challenge),
    )
    assert captured["retained_manifest_sha256"] == result.challenge.manifest_sha256
    assert captured["retained_manifest"].canonical_sha256


def test_usb_identity_mismatch_fails_before_adapter_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _, _ = _make_files(tmp_path)
    config = load_hardware_identification_config(config_path)
    RecordingMaestro.constructed.clear()
    monkeypatch.setattr(module, "MaestroAdapter", RecordingMaestro)
    monkeypatch.setattr(
        module,
        "_resolve_linux_usb_identity",
        lambda _: LinuxUsbIdentity(
            serial_number="wrong", interface_number="02", resolved_tty="/dev/ttyACM1"
        ),
    )
    attestation = _attestation(config)
    approval = _approval(config_path, config)
    with pytest.raises(ValueError, match="USB identity"):
        prepare_hardware_identification(
            config_path=config_path,
            manifest_path=MANIFEST_PATH,
            approval=approval,
            attestation=attestation,
            enable_hardware=True,
        )
    assert RecordingMaestro.constructed == []


def test_stale_approval_is_rejected_before_usb_or_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _, _ = _make_files(tmp_path)
    config = load_hardware_identification_config(config_path)
    attestation = _attestation(config)
    approval = _approval(config_path, config).model_copy(
        update={
            "approved_at": datetime.now(UTC) - timedelta(seconds=61),
            "approved_monotonic_ns": time.monotonic_ns() - 61_000_000_000,
        }
    )
    resolved = False

    def forbidden_resolver(_: str) -> LinuxUsbIdentity:
        nonlocal resolved
        resolved = True
        raise AssertionError

    monkeypatch.setattr(module, "_resolve_linux_usb_identity", forbidden_resolver)
    with pytest.raises(ValueError, match="approval is stale"):
        prepare_hardware_identification(
            config_path=config_path,
            manifest_path=MANIFEST_PATH,
            approval=approval,
            attestation=attestation,
            enable_hardware=True,
        )
    assert resolved is False


def test_keyboard_interrupt_publishes_sanitized_failure(
    prepared, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _, _ = prepared
    monkeypatch.setattr(
        module,
        "_run_identification_core",
        lambda **_: (_ for _ in ()).throw(KeyboardInterrupt("secret detail")),
    )
    output = tmp_path / "failed"
    with pytest.raises(KeyboardInterrupt, match="secret detail"):
        execute_prepared_hardware_identification(
            prepared=result,
            observer=object(),
            output_dir=output,
            confirmation=_power_confirmation(result.challenge),
        )
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "aborted"
    assert manifest["failure"]["error_type"] == "KeyboardInterrupt"
    assert "secret detail" not in json.dumps(manifest)
    assert "enable_token" not in manifest["config"]
    for artifact in output.iterdir():
        content = artifact.read_text()
        assert "run-secret-not-in-repository" not in content
        assert "enable_token" not in content
        assert "confirmation_text" not in content
        assert result.issuance_token.get_secret_value() not in content
    assert RecordingMaestro.instances[-1].closed is True


def test_repository_config_retains_unresolved_electrical_placeholders() -> None:
    config = load_hardware_identification_config(CONFIG_TEMPLATE)
    assert config.electrical_evidence_path.startswith("REQUIRED_")
    assert config.electrical_evidence_sha256.startswith("REQUIRED_")


def test_repository_config_refuses_before_usb_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_hardware_identification_config(CONFIG_TEMPLATE)
    resolved = False

    def forbidden(_: str) -> LinuxUsbIdentity:
        nonlocal resolved
        resolved = True
        raise AssertionError

    monkeypatch.setattr(module, "_resolve_linux_usb_identity", forbidden)
    with pytest.raises(ValueError, match="electrical evidence path is unresolved"):
        prepare_hardware_identification(
            config_path=CONFIG_TEMPLATE,
            manifest_path=MANIFEST_PATH,
            approval=None,
            attestation=_attestation(config),
            enable_hardware=True,
        )
    assert resolved is False


def test_electrical_source_document_hash_is_verified_before_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _, evidence = _make_files(tmp_path)
    Path(evidence.source).write_bytes(b"changed after review")
    config = load_hardware_identification_config(config_path)
    RecordingMaestro.constructed.clear()
    monkeypatch.setattr(module, "MaestroAdapter", RecordingMaestro)
    attestation = _attestation(config)
    approval = _approval(config_path, config)
    with pytest.raises(ValueError, match="source document checksum"):
        prepare_hardware_identification(
            config_path=config_path,
            manifest_path=MANIFEST_PATH,
            approval=approval,
            attestation=attestation,
            enable_hardware=True,
        )
    assert RecordingMaestro.constructed == []


def test_watchdog_revokes_then_closes_on_os_monotonic_deadline() -> None:
    events: list[str] = []
    watchdog = module.IndependentHardwareWatchdog(
        timeout_seconds=0.02,
        revoke=lambda: events.append("revoke"),
        close=lambda: events.append("close"),
    )
    watchdog.start()
    deadline = time.monotonic() + 1.0
    while len(events) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    watchdog.stop()
    assert events == ["revoke", "close"]


def test_serial_runtime_failure_is_published_without_replacing_primary(
    prepared, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _, _ = prepared
    monkeypatch.setattr(
        module,
        "_run_identification_core",
        lambda **_: (_ for _ in ()).throw(RuntimeError("serial private detail")),
    )
    output = tmp_path / "serial-failed"
    with pytest.raises(RuntimeError, match="serial private detail"):
        execute_prepared_hardware_identification(
            prepared=result,
            observer=object(),
            output_dir=output,
            confirmation=_power_confirmation(result.challenge),
        )
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["failure"]["error_type"] == "RuntimeError"
    assert "serial private detail" not in json.dumps(manifest)


def test_watchdog_failure_revokes_closes_and_publishes(
    prepared, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _, _ = prepared

    class TriggeringWatchdog:
        def __init__(
            self,
            *,
            timeout_seconds: float,
            revoke,
            close,
        ) -> None:
            assert timeout_seconds > 0
            self.revoke = revoke
            self.close = close

        def start(self) -> None:
            self.revoke()
            self.close()

        def pet(self) -> None:
            pass

        def stop(self) -> None:
            pass

    def watchdog_interrupted_core(**_: object) -> object:
        assert RecordingMaestro.instances[-1].closed is True
        raise RuntimeError("watchdog interrupted execution")

    monkeypatch.setattr(module, "IndependentHardwareWatchdog", TriggeringWatchdog)
    monkeypatch.setattr(
        module, "_run_identification_core", watchdog_interrupted_core
    )
    output = tmp_path / "watchdog-failed"
    with pytest.raises(RuntimeError, match="watchdog interrupted"):
        execute_prepared_hardware_identification(
            prepared=result,
            observer=object(),
            output_dir=output,
            confirmation=_power_confirmation(result.challenge),
        )
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "aborted"
    assert RecordingMaestro.instances[-1].closed is True


def test_cancel_is_single_use_and_closes_without_motion(prepared) -> None:
    result, _, _ = prepared
    cancel_prepared_hardware_identification(result)
    assert RecordingMaestro.instances[-1].closed is True
    assert RecordingMaestro.instances[-1].writes == 0
    with pytest.raises(ValueError, match="consumed"):
        cancel_prepared_hardware_identification(result)


def test_abandoned_prepared_challenge_expires_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _, _ = _make_files(tmp_path)
    config_path.write_text(
        config_path.read_text().replace(
            "power_enable_challenge_ttl_ms: 30000",
            "power_enable_challenge_ttl_ms: 20",
        )
    )
    config = load_hardware_identification_config(config_path)
    RecordingMaestro.constructed.clear()
    RecordingMaestro.instances.clear()
    monkeypatch.setattr(module, "MaestroAdapter", RecordingMaestro)
    monkeypatch.setattr(
        module,
        "_resolve_linux_usb_identity",
        lambda _: LinuxUsbIdentity(
            serial_number="00037376",
            interface_number="00",
            resolved_tty="/dev/ttyACM0",
        ),
    )
    attestation = _attestation(config)
    result = prepare_hardware_identification(
        config_path=config_path,
        manifest_path=MANIFEST_PATH,
        approval=_approval(config_path, config),
        attestation=attestation,
        enable_hardware=True,
    )
    deadline = time.monotonic() + 1.0
    while not RecordingMaestro.instances[-1].closed and time.monotonic() < deadline:
        time.sleep(0.005)
    assert RecordingMaestro.instances[-1].closed is True
    with pytest.raises(ValueError, match="consumed"):
        cancel_prepared_hardware_identification(result)


def test_dropped_handle_finalizer_closes_private_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, _, _ = _make_files(tmp_path)
    config = load_hardware_identification_config(config_path)
    RecordingMaestro.constructed.clear()
    RecordingMaestro.instances.clear()
    monkeypatch.setattr(module, "MaestroAdapter", RecordingMaestro)
    monkeypatch.setattr(
        module,
        "_resolve_linux_usb_identity",
        lambda _: LinuxUsbIdentity(
            serial_number="00037376",
            interface_number="00",
            resolved_tty="/dev/ttyACM0",
        ),
    )
    attestation = _attestation(config)
    handle = prepare_hardware_identification(
        config_path=config_path,
        manifest_path=MANIFEST_PATH,
        approval=_approval(config_path, config),
        attestation=attestation,
        enable_hardware=True,
    )
    del handle
    gc.collect()
    deadline = time.monotonic() + 1.0
    while not RecordingMaestro.instances[-1].closed and time.monotonic() < deadline:
        time.sleep(0.005)
    assert RecordingMaestro.instances[-1].closed is True
