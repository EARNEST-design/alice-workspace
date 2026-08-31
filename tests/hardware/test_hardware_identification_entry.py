from __future__ import annotations

import hashlib
import inspect
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

import alice.experiments.hardware_identification as module
from alice.experiments.hardware_identification import (
    ElectricalSafetyEvidence,
    HardwareApproval,
    HardwareIdentificationConfig,
    HardwarePreflightAttestation,
    LinuxUsbIdentity,
    PowerEnableConfirmation,
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

    def close(self) -> None:
        self.closed = True


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
    result.cancel_if_open()


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

    def fake_core(**_: object) -> object:
        nonlocal called
        called = True
        return object()

    monkeypatch.setattr(module, "_run_identification_core", fake_core)
    execute_prepared_hardware_identification(
        prepared=result,
        observer=object(),
        output_dir=tmp_path / "run",
        confirmation=_power_confirmation(result.challenge),
    )
    assert called is True
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
    assert manifest["config"]["enable_token"] == "**********"
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
