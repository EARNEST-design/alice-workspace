from __future__ import annotations

import inspect
import time
from pathlib import Path

import pytest

import alice.experiments.hardware_identification as hardware_identification_module
from alice.experiments.hardware_identification import (
    HardwareApproval,
    HardwareIdentificationConfig,
    HardwarePreflightAttestation,
    IndependentHardwareWatchdog,
    load_hardware_identification_config,
    prepare_hardware_identification,
    run_hardware_identification,
)
from alice.hardware.maestro_adapter import MaestroPreflightSnapshot

ROOT = Path(__file__).parents[2]
CONFIG_PATH = ROOT / "config/experiments/actuator-identification-hardware.yaml"
MANIFEST_PATH = ROOT / "hardware/alice-face-v1.yaml"


def _valid_attestation(
    config: HardwareIdentificationConfig,
) -> HardwarePreflightAttestation:
    return HardwarePreflightAttestation(
        run_id=config.run_id,
        observed_monotonic_ns=time.monotonic_ns(),
        master_servo_power_removed=True,
        emergency_power_removal_ready=True,
        electrical_current_limit_verified=True,
        mechanical_clearance_verified=True,
        channel_10_linkage_verified=True,
        command_interface_role_verified=True,
        no_competing_processes=True,
        phase_1_camera_accepted=True,
    )


def test_repository_hardware_config_is_deliberately_non_executable() -> None:
    config = load_hardware_identification_config(CONFIG_PATH)

    assert config.adapter == "maestro"
    assert config.approval_id.startswith("REQUIRED_")
    assert config.enable_token.get_secret_value().startswith("REQUIRED_")
    assert config.actuator_names == ("mouth_open",)
    assert config.offsets == (0.05, -0.05)

    with pytest.raises(ValueError, match="placeholder"):
        prepare_hardware_identification(
            config_path=CONFIG_PATH,
            manifest_path=MANIFEST_PATH,
            approval=HardwareApproval(
                approval_id="approval-for-test",
                enable_token="token-for-test",
                config_sha256="0" * 64,
                manifest_sha256="0" * 64,
                confirmation_text="irrelevant",
            ),
            attestation=_valid_attestation(config),
            enable_hardware=True,
        )


def test_preparation_requires_flag_exact_hashes_and_hash_bound_confirmation(
    tmp_path: Path,
) -> None:
    text = (
        CONFIG_PATH.read_text()
        .replace("REQUIRED_RUN_SPECIFIC_RUN_ID", "hardware-run-20260901-001")
        .replace("REQUIRED_RUN_SPECIFIC_APPROVAL_ID", "review-20260901-operator")
        .replace(
            "REQUIRED_RUN_SPECIFIC_ENABLE_TOKEN", "run-secret-not-in-repository"
        )
    )
    config_path = tmp_path / "hardware.yaml"
    config_path.write_text(text)
    config = load_hardware_identification_config(config_path)
    attestation = _valid_attestation(config)
    assert "run-secret-not-in-repository" not in str(config.model_dump(mode="json"))

    with pytest.raises(ValueError, match="--enable-hardware"):
        prepare_hardware_identification(
            config_path=config_path,
            manifest_path=MANIFEST_PATH,
            approval=None,
            attestation=attestation,
            enable_hardware=False,
        )

    config_hash = __import__("hashlib").sha256(config_path.read_bytes()).hexdigest()
    manifest_hash = __import__("hashlib").sha256(MANIFEST_PATH.read_bytes()).hexdigest()
    confirmation = (
        f"ENABLE {config.run_id} CONFIG {config_hash} MANIFEST {manifest_hash}"
    )
    approval = HardwareApproval(
        approval_id=config.approval_id,
        enable_token=config.enable_token,
        config_sha256=config_hash,
        manifest_sha256=manifest_hash,
        confirmation_text=confirmation,
    )

    prepared = prepare_hardware_identification(
        config_path=config_path,
        manifest_path=MANIFEST_PATH,
        approval=approval,
        attestation=attestation,
        enable_hardware=True,
    )
    assert prepared.config_sha256 == config_hash
    assert prepared.manifest_sha256 == manifest_hash
    assert prepared.confirmation_text == confirmation

    changed = approval.model_copy(update={"confirmation_text": confirmation + " "})
    with pytest.raises(ValueError, match="confirmation"):
        prepare_hardware_identification(
            config_path=config_path,
            manifest_path=MANIFEST_PATH,
            approval=changed,
            attestation=attestation,
            enable_hardware=True,
        )


def test_public_hardware_root_has_no_adapter_transport_or_factory_injection() -> None:
    parameters = inspect.signature(run_hardware_identification).parameters

    assert "adapter" not in parameters
    assert "adapter_factory" not in parameters
    assert "transport" not in parameters
    assert "transport_factory" not in parameters
    assert "supervisor" not in parameters
    assert "clock" not in parameters
    assert "sleeper" not in parameters


def test_independent_watchdog_uses_os_clock_and_revokes_and_closes() -> None:
    events: list[str] = []
    watchdog = IndependentHardwareWatchdog(
        timeout_seconds=0.03,
        revoke=lambda: events.append("revoke"),
        close=lambda: events.append("close"),
    )

    watchdog.start()
    deadline = time.monotonic() + 1.0
    while len(events) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    watchdog.stop()

    assert events == ["revoke", "close"]


def test_missing_attestation_fails_before_hardware_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = False

    class ForbiddenAdapter:
        def __init__(self, **_: object) -> None:
            nonlocal opened
            opened = True

    monkeypatch.setattr(
        hardware_identification_module, "MaestroAdapter", ForbiddenAdapter
    )
    with pytest.raises(ValueError):
        run_hardware_identification(
            config_path=CONFIG_PATH,
            manifest_path=MANIFEST_PATH,
            output_dir=Path("unused"),
            observer=object(),
            approval=None,
            attestation=None,
            enable_hardware=False,
        )
    assert opened is False


def test_trusted_composition_constructs_exact_maestro_with_consume_only_verifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "hardware.yaml"
    config_path.write_text(
        CONFIG_PATH.read_text()
        .replace("REQUIRED_RUN_SPECIFIC_RUN_ID", "hardware-run-20260901-001")
        .replace("REQUIRED_RUN_SPECIFIC_APPROVAL_ID", "review-20260901-operator")
        .replace(
            "REQUIRED_RUN_SPECIFIC_ENABLE_TOKEN", "run-secret-not-in-repository"
        )
    )
    config = load_hardware_identification_config(config_path)
    config_hash = __import__("hashlib").sha256(config_path.read_bytes()).hexdigest()
    manifest_hash = __import__("hashlib").sha256(MANIFEST_PATH.read_bytes()).hexdigest()
    phrase = f"ENABLE {config.run_id} CONFIG {config_hash} MANIFEST {manifest_hash}"
    captured: dict[str, object] = {}

    class RecordingMaestro:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.closed = False

        def open(self, token: str) -> None:
            assert token == "run-secret-not-in-repository"

        def read_only_preflight(
            self, names: tuple[str, ...]
        ) -> MaestroPreflightSnapshot:
            assert names == ("mouth_open",)
            return MaestroPreflightSnapshot(
                controller_error_register=0,
                positions_qus={"mouth_open": 5059},
                observed_monotonic_ns=time.monotonic_ns(),
            )

        def close(self) -> None:
            self.closed = True

    sentinel = object()

    def fake_core(**kwargs: object) -> object:
        supervisor = kwargs["supervisor"]
        assert captured["permit_verifier"] is supervisor.actuation_permit_verifier
        return sentinel

    monkeypatch.setattr(
        hardware_identification_module, "MaestroAdapter", RecordingMaestro
    )
    monkeypatch.setattr(
        hardware_identification_module, "_run_identification_core", fake_core
    )
    result = run_hardware_identification(
        config_path=config_path,
        manifest_path=MANIFEST_PATH,
        output_dir=tmp_path / "output",
        observer=object(),
        approval=HardwareApproval(
            approval_id=config.approval_id,
            enable_token="run-secret-not-in-repository",
            config_sha256=config_hash,
            manifest_sha256=manifest_hash,
            confirmation_text=phrase,
        ),
        attestation=_valid_attestation(config),
        enable_hardware=True,
    )

    assert result is sentinel
    assert captured["stable_device_path"] == config.stable_device_path
    assert captured["expected_controller_serial"] == "00037376"
