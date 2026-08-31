"""Capability-gated composition for a reviewed Maestro identification run."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from alice.experiments.manifest import ArtifactManifest
from alice.experiments.system_identification import (
    IdentificationConfig,
    IdentificationObserver,
    _run_identification_core,
)
from alice.hardware.adapter import ActuatorAuthorization, AdapterIdentity
from alice.hardware.maestro_adapter import MaestroAdapter
from alice.hardware.manifest import HardwareManifest, load_manifest
from alice.safety.supervisor import (
    OperatorApproval,
    PreflightEvidence,
    RunState,
    SafetyLimits,
    SafetySupervisor,
)

_PLACEHOLDER_PREFIX = "REQUIRED_"


class HardwareIdentificationConfig(IdentificationConfig):
    """Reviewed hardware-only run configuration with invalid repository secrets."""

    adapter: Literal["maestro"]  # type: ignore[assignment]
    stable_device_path: str
    expected_controller_serial: str
    approval_id: str
    enable_token: SecretStr
    home_tolerance_qus: Annotated[int, Field(ge=0)]
    independent_watchdog_ms: Annotated[int, Field(gt=0)]
    safety_limits: SafetyLimits

    @model_validator(mode="after")
    def validate_hardware_identity(self) -> HardwareIdentificationConfig:
        if not self.stable_device_path.endswith("00037376-if00"):
            raise ValueError("exact reviewed interface-00 stable path is required")
        if self.expected_controller_serial != "00037376":
            raise ValueError("reviewed controller serial is required")
        if self.independent_watchdog_ms >= self.step_timeout_ms:
            raise ValueError("independent watchdog must precede the step timeout")
        return self


class HardwarePreflightAttestation(BaseModel):
    """Contemporaneous operator facts gathered before opening the command port."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    observed_monotonic_ns: Annotated[int, Field(ge=0)]
    master_servo_power_removed: bool
    emergency_power_removal_ready: bool
    electrical_current_limit_verified: bool
    mechanical_clearance_verified: bool
    channel_10_linkage_verified: bool
    command_interface_role_verified: bool
    no_competing_processes: bool
    phase_1_camera_accepted: bool

    @property
    def requirement_results(self) -> Mapping[str, bool]:
        return {
            "emergency-power-removal-verified": self.emergency_power_removal_ready,
            "electrical-current-limit-verified": self.electrical_current_limit_verified,
            "mechanical-clearance-verified": self.mechanical_clearance_verified,
            "channel-10-linkage-inspection": self.channel_10_linkage_verified,
            "maestro-command-interface-role-verified": (
                self.command_interface_role_verified
            ),
        }


class HardwareApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: str
    enable_token: SecretStr
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_text: str


class PreparedHardwareRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    config_sha256: str
    manifest_sha256: str
    confirmation_text: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_hardware_identification_config(
    path: str | Path,
) -> HardwareIdentificationConfig:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("hardware identification config must be a mapping")
    return HardwareIdentificationConfig.model_validate(document)


def expected_confirmation(run_id: str, config_sha256: str, manifest_sha256: str) -> str:
    return f"ENABLE {run_id} CONFIG {config_sha256} MANIFEST {manifest_sha256}"


def prepare_hardware_identification(
    *,
    config_path: str | Path,
    manifest_path: str | Path,
    approval: HardwareApproval | None,
    attestation: HardwarePreflightAttestation,
    enable_hardware: bool,
) -> PreparedHardwareRun:
    """Validate every non-device gate. This function never opens serial."""

    config_file, manifest_file = Path(config_path), Path(manifest_path)
    config = load_hardware_identification_config(config_file)
    manifest = load_manifest(manifest_file)
    config_hash, manifest_hash = _sha256(config_file), _sha256(manifest_file)
    if not enable_hardware:
        raise ValueError("explicit --enable-hardware flag is required")
    config_token = config.enable_token.get_secret_value()
    has_placeholder = any(
        value.startswith(_PLACEHOLDER_PREFIX)
        for value in (config.run_id, config.approval_id, config_token)
    )
    if has_placeholder:
        raise ValueError("repository hardware config contains a REQUIRED placeholder")
    if approval is None:
        raise ValueError("run-specific operator approval is required")
    if (
        approval.approval_id != config.approval_id
        or approval.enable_token.get_secret_value() != config_token
    ):
        raise ValueError("approval identity or enable token mismatch")
    if (
        approval.config_sha256 != config_hash
        or approval.manifest_sha256 != manifest_hash
    ):
        raise ValueError("approval checksum mismatch")
    expected = expected_confirmation(config.run_id, config_hash, manifest_hash)
    if approval.confirmation_text != expected:
        raise ValueError("interactive confirmation does not bind exact hashes")
    if manifest_hash != config.hardware_manifest_sha256:
        raise ValueError("hardware manifest file checksum mismatch")
    if manifest.canonical_sha256 != config.hardware_manifest_canonical_sha256:
        raise ValueError("hardware manifest canonical checksum mismatch")
    if manifest.calibration_sha256 != config.calibration_sha256:
        raise ValueError("calibration checksum mismatch")
    if config.stable_device_path != manifest.controller.command_device_path:
        raise ValueError("stable device path mismatch")
    if config.expected_controller_serial != manifest.controller.serial_number:
        raise ValueError("controller serial mismatch")
    if attestation.run_id != config.run_id:
        raise ValueError("preflight run identity mismatch")
    age = time.monotonic_ns() - attestation.observed_monotonic_ns
    if age < 0 or age >= config.safety_limits.preflight_max_age_ns:
        raise ValueError("preflight attestation is stale")
    if not attestation.master_servo_power_removed:
        raise ValueError("master servo power must remain removed during preparation")
    if not all(attestation.requirement_results.values()):
        raise ValueError("one or more physical preflight requirements are unmet")
    if not attestation.no_competing_processes:
        raise ValueError("competing actuator process check failed")
    if not attestation.phase_1_camera_accepted:
        raise ValueError("Phase 1 camera acceptance is required")
    return PreparedHardwareRun(
        run_id=config.run_id,
        config_sha256=config_hash,
        manifest_sha256=manifest_hash,
        confirmation_text=expected,
    )


class IndependentHardwareWatchdog:
    """OS-monotonic, process-local revocation and serial-close authority."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        revoke: Callable[[], object],
        close: Callable[[], object],
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("watchdog timeout must be positive")
        self._timeout = timeout_seconds
        self._revoke = revoke
        self._close = close
        self._condition = threading.Condition()
        self._deadline = time.monotonic() + timeout_seconds
        self._stopped = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("watchdog already started")
            self._deadline = time.monotonic() + self._timeout
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def pet(self) -> None:
        with self._condition:
            if not self._stopped:
                self._deadline = time.monotonic() + self._timeout
                self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self._timeout * 2))

    def _run(self) -> None:
        with self._condition:
            while not self._stopped:
                remaining = self._deadline - time.monotonic()
                if remaining > 0:
                    self._condition.wait(timeout=remaining)
                    continue
                self._stopped = True
                break
        try:
            self._revoke()
        finally:
            self._close()


class _WatchedAdapter:
    def __init__(
        self, adapter: MaestroAdapter, watchdog: IndependentHardwareWatchdog
    ) -> None:
        self._adapter = adapter
        self._watchdog = watchdog

    @property
    def identity(self) -> AdapterIdentity:
        return self._adapter.identity

    def apply(self, authorization: ActuatorAuthorization):  # type: ignore[no-untyped-def]
        self._watchdog.pet()
        result = self._adapter.apply(authorization)
        self._watchdog.pet()
        return result

    def close(self) -> None:
        self._adapter.close()


class _WatchedObserver:
    def __init__(
        self,
        observer: IdentificationObserver,
        watchdog: IndependentHardwareWatchdog,
    ) -> None:
        self._observer = observer
        self._watchdog = watchdog

    @property
    def provenance(self):  # type: ignore[no-untyped-def]
        return self._observer.provenance

    def observe(self, *, run_id: str, step_id: str):  # type: ignore[no-untyped-def]
        self._watchdog.pet()
        result = self._observer.observe(run_id=run_id, step_id=step_id)
        self._watchdog.pet()
        return result


def run_hardware_identification(
    *,
    config_path: str | Path,
    manifest_path: str | Path,
    output_dir: Path,
    observer: IdentificationObserver,
    approval: HardwareApproval | None,
    attestation: HardwarePreflightAttestation | None,
    enable_hardware: bool,
) -> ArtifactManifest:
    """Trusted hardware root; no adapter, supervisor, transport, or clock seam."""

    if attestation is None:
        raise ValueError("contemporaneous preflight attestation is required")
    prepare_hardware_identification(
        config_path=config_path,
        manifest_path=manifest_path,
        approval=approval,
        attestation=attestation,
        enable_hardware=enable_hardware,
    )
    assert approval is not None
    config = load_hardware_identification_config(config_path)
    manifest: HardwareManifest = load_manifest(manifest_path)
    supervisor = SafetySupervisor(
        manifest=manifest, limits=config.safety_limits, clock=time.monotonic_ns
    )
    adapter = MaestroAdapter(
        manifest=manifest,
        stable_device_path=manifest.controller.command_device_path,
        expected_controller_serial=manifest.controller.serial_number,
        required_enable_token=config.enable_token.get_secret_value(),
        clock=time.monotonic_ns,
        permit_verifier=supervisor.actuation_permit_verifier,
        settle_timeout_ns=config.controller_settle_ms * 1_000_000,
    )
    watchdog = IndependentHardwareWatchdog(
        timeout_seconds=config.independent_watchdog_ms / 1_000,
        revoke=lambda: supervisor.revoke_external_authority(
            "independent OS-monotonic watchdog expired"
        ),
        close=adapter.close,
    )
    try:
        # Opening is delayed until every non-device gate above has passed.
        adapter.open(approval.enable_token.get_secret_value())
        snapshot = adapter.read_only_preflight(config.actuator_names)
        home_ok = all(
            abs(snapshot.positions_qus[name] - manifest.actuator(name).home_qus)
            <= config.home_tolerance_qus
            for name in config.actuator_names
        )
        evidence = PreflightEvidence(
            run_id=config.run_id,
            hardware_id=manifest.hardware_id,
            calibration_sha256=manifest.calibration_sha256,
            controller_serial=manifest.controller.serial_number,
            requirement_results=attestation.requirement_results,
            competing_process_detected=not attestation.no_competing_processes,
            controller_error_codes=(
                (snapshot.controller_error_register,)
                if snapshot.controller_error_register
                else ()
            ),
            home_verified=home_ok,
            observed_monotonic_ns=snapshot.observed_monotonic_ns,
        )
        preflight = supervisor.preflight(evidence)
        if not preflight.accepted:
            raise RuntimeError("device-reading preflight rejected")
        armed = supervisor.arm(
            OperatorApproval(
                approval_id=approval.approval_id,
                run_id=config.run_id,
                confirmed_monotonic_ns=time.monotonic_ns(),
            )
        )
        if not armed.accepted or armed.state is not RunState.ARMED:
            raise RuntimeError("supervisor arm rejected")
        watchdog.start()
        return _run_identification_core(
            config=config,
            observer=_WatchedObserver(observer, watchdog),
            supervisor=supervisor,
            adapter=_WatchedAdapter(adapter, watchdog),
            output_dir=output_dir,
            clock=time.monotonic_ns,
            sleeper=time.sleep,
        )
    except BaseException:
        supervisor.revoke_external_authority(
            "hardware composition exited exceptionally; position may be unknown"
        )
        raise
    finally:
        watchdog.stop()
        adapter.close()
