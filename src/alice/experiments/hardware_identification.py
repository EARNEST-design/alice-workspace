"""Two-stage, capability-gated Maestro identification composition."""

from __future__ import annotations

import hashlib
import json
import platform
import secrets
import subprocess
import threading
import time
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    model_validator,
)

from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.experiments.artifact_store import publish_generation, sha256_path
from alice.experiments.manifest import (
    ArtifactManifest,
    ArtifactRecord,
    ControllerPreflightPosition,
    ElectricalSafetyProvenance,
    FailureCategory,
    FailureRecord,
    HardwareApprovalProvenance,
    HardwareIdentificationProvenance,
    IdentificationObserverProvenance,
    IdentificationRunMetadata,
    IndependentWatchdogProvenance,
    NegotiatedCameraSettings,
    PowerChallengeProvenance,
    PowerConfirmationProvenance,
    ReadOnlyControllerPreflightProvenance,
    RunKind,
    RunStatus,
    UsbIdentityProvenance,
)
from alice.experiments.system_identification import (
    IdentificationConfig,
    IdentificationObserver,
    _run_identification_core,
)
from alice.hardware.adapter import ActuatorAuthorization, AdapterIdentity
from alice.hardware.maestro_adapter import MaestroAdapter, MaestroPreflightSnapshot
from alice.hardware.manifest import HardwareManifest
from alice.safety.supervisor import (
    OperatorApproval,
    PreflightEvidence,
    RunState,
    SafetyLimits,
    SafetySupervisor,
)

_PLACEHOLDER_PREFIX = "REQUIRED_"
_PREFLIGHT_ACK = "I CONFIRM PREFLIGHT WITH MASTER SERVO POWER OFF"
_APPROVAL_ACK = "I APPROVE READ-ONLY PREFLIGHT WITH SERVO POWER OFF"
_POWER_ACK = "I CONFIRM MASTER SERVO POWER IS ON AND POWER REMOVAL IS READY"


class HardwareIdentificationConfig(IdentificationConfig):
    """Reviewed hardware config; repository placeholders keep it unusable."""

    adapter: Literal["maestro"]  # type: ignore[assignment]
    stable_device_path: NonEmptyString
    expected_controller_serial: NonEmptyString
    expected_usb_interface: Literal["00"]
    approval_id: NonEmptyString
    enable_token: SecretStr
    electrical_evidence_path: NonEmptyString
    electrical_evidence_sha256: str
    electrical_review_max_age_days: Annotated[int, Field(gt=0)]
    home_tolerance_qus: Annotated[int, Field(ge=0)]
    independent_watchdog_ms: Annotated[int, Field(gt=0)]
    power_enable_challenge_ttl_ms: Annotated[int, Field(gt=0)]
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


class _HardwareExecutionConfig(IdentificationConfig):
    """Non-secret immutable configuration serialized into run artifacts."""

    adapter: Literal["maestro"]  # type: ignore[assignment]
    stable_device_path: NonEmptyString
    expected_controller_serial: NonEmptyString
    expected_usb_interface: Literal["00"]
    approval_id: NonEmptyString
    electrical_evidence_sha256: Sha256Hex
    electrical_review_max_age_days: Annotated[int, Field(gt=0)]
    home_tolerance_qus: Annotated[int, Field(ge=0)]
    independent_watchdog_ms: Annotated[int, Field(gt=0)]
    power_enable_challenge_ttl_ms: Annotated[int, Field(gt=0)]
    safety_limits: SafetyLimits


class ElectricalSafetyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["electrical-safety-evidence/v1"]
    evidence_id: NonEmptyString
    source: NonEmptyString
    source_document_sha256: Sha256Hex
    reviewed_at: AwareDatetime
    reviewer: NonEmptyString
    supply_voltage_v: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    current_limit_a: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    scope: NonEmptyString


class HardwarePreflightAttestation(BaseModel):
    """Contemporaneous operator facts gathered with servo power removed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: NonEmptyString
    observed_at: AwareDatetime
    observed_monotonic_ns: Annotated[int, Field(ge=0)]
    source: NonEmptyString
    operator_acknowledgment: Literal[
        "I CONFIRM PREFLIGHT WITH MASTER SERVO POWER OFF"
    ]
    master_servo_power_removed: Literal[True]
    emergency_power_removal_ready: Literal[True]
    mechanical_clearance_verified: Literal[True]
    channel_10_linkage_verified: Literal[True]
    command_interface_role_verified: Literal[True]
    no_competing_processes: Literal[True]
    phase_1_camera_accepted: Literal[True]

    @property
    def requirement_results(self) -> Mapping[str, bool]:
        return {
            "emergency-power-removal-verified": True,
            "electrical-current-limit-verified": True,
            "mechanical-clearance-verified": True,
            "channel-10-linkage-inspection": True,
            "maestro-command-interface-role-verified": True,
        }


class HardwareApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: NonEmptyString
    enable_token: SecretStr
    run_id: NonEmptyString
    config_sha256: Sha256Hex
    manifest_sha256: Sha256Hex
    electrical_evidence_sha256: Sha256Hex
    approved_at: AwareDatetime
    approved_monotonic_ns: Annotated[int, Field(ge=0)]
    source: NonEmptyString
    operator_acknowledgment: Literal[
        "I APPROVE READ-ONLY PREFLIGHT WITH SERVO POWER OFF"
    ]
    confirmation_text: NonEmptyString


class LinuxUsbIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    serial_number: NonEmptyString
    interface_number: NonEmptyString
    resolved_tty: NonEmptyString


class PowerEnableChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    challenge_id: NonEmptyString
    run_id: NonEmptyString
    config_sha256: Sha256Hex
    manifest_sha256: Sha256Hex
    electrical_evidence_sha256: Sha256Hex
    issued_at: AwareDatetime
    issued_monotonic_ns: Annotated[int, Field(ge=0)]
    expires_monotonic_ns: Annotated[int, Field(gt=0)]
    challenge_sha256: Sha256Hex


class PowerEnableConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: NonEmptyString
    challenge_id: NonEmptyString
    config_sha256: Sha256Hex
    manifest_sha256: Sha256Hex
    electrical_evidence_sha256: Sha256Hex
    challenge_sha256: Sha256Hex
    confirmed_at: AwareDatetime
    confirmed_monotonic_ns: Annotated[int, Field(ge=0)]
    source: NonEmptyString
    operator_acknowledgment: Literal[
        "I CONFIRM MASTER SERVO POWER IS ON AND POWER REMOVAL IS READY"
    ]


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
            try:
                self._close()
            except Exception:
                pass


class _PreparedState:
    """Module-private ownership of a read-only-preflighted serial session."""

    __slots__ = (
        "_adapter",
        "_approval",
        "_config",
        "_config_bytes",
        "_electrical_evidence",
        "_electrical_evidence_bytes",
        "_electrical_source_bytes",
        "_execution_config",
        "_manifest",
        "_manifest_bytes",
        "_preflight",
        "_read_only_snapshot",
        "_usb_identity",
        "_started_at",
        "_supervisor",
        "challenge",
        "timer",
    )

    def __init__(
        self,
        *,
        challenge: PowerEnableChallenge,
        config: HardwareIdentificationConfig,
        config_bytes: bytes,
        execution_config: _HardwareExecutionConfig,
        manifest: HardwareManifest,
        manifest_bytes: bytes,
        electrical_evidence: ElectricalSafetyEvidence,
        electrical_evidence_bytes: bytes,
        electrical_source_bytes: bytes,
        approval: HardwareApproval,
        preflight: PreflightEvidence,
        read_only_snapshot: MaestroPreflightSnapshot,
        usb_identity: LinuxUsbIdentity,
        supervisor: SafetySupervisor,
        adapter: MaestroAdapter,
    ) -> None:
        self.challenge = challenge
        self._config = config
        self._config_bytes = config_bytes
        self._execution_config = execution_config
        self._manifest = manifest
        self._manifest_bytes = manifest_bytes
        self._electrical_evidence = electrical_evidence
        self._electrical_evidence_bytes = electrical_evidence_bytes
        self._electrical_source_bytes = electrical_source_bytes
        self._approval = approval
        self._preflight = preflight
        self._read_only_snapshot = read_only_snapshot
        self._usb_identity = usb_identity
        self._supervisor = supervisor
        self._adapter = adapter
        self._started_at = datetime.now(UTC)
        self.timer: threading.Timer | None = None


@dataclass(frozen=True, slots=True, weakref_slot=True)
class PreparedHardwareHandle:
    """Display-only challenge and opaque same-process issuance capability."""

    challenge: PowerEnableChallenge
    issuance_token: SecretStr


_registry_lock = threading.Lock()
_prepared_registry: dict[str, _PreparedState] = {}


def _close_state(state: _PreparedState, detail: str) -> None:
    try:
        state._supervisor.revoke_external_authority(detail)
    except Exception:
        pass
    try:
        state._adapter.close()
    except Exception:
        pass


def _remove_state(token: str) -> _PreparedState | None:
    with _registry_lock:
        state = _prepared_registry.pop(token, None)
    if state is not None and state.timer is not None:
        state.timer.cancel()
    return state


def _expire_or_abandon(token: str) -> None:
    state = _remove_state(token)
    if state is not None:
        _close_state(state, "prepared hardware handle expired or was abandoned")


def _challenge_digest(challenge: PowerEnableChallenge) -> str:
    payload = challenge.model_dump(mode="json", exclude={"challenge_sha256"})
    return _sha256_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )


def _consume_handle(handle: PreparedHardwareHandle) -> _PreparedState:
    token = handle.issuance_token.get_secret_value()
    state = _remove_state(token)
    if state is None:
        raise ValueError("prepared hardware handle is unknown or consumed")
    challenge_ok = (
        handle.challenge == state.challenge
        and _challenge_digest(handle.challenge) == handle.challenge.challenge_sha256
        and _challenge_digest(state.challenge) == state.challenge.challenge_sha256
    )
    if not challenge_ok:
        _close_state(state, "prepared hardware challenge integrity failed")
        raise ValueError("prepared hardware challenge was mutated or forged")
    return state


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _yaml_mapping(payload: bytes, description: str) -> dict[str, object]:
    document = yaml.safe_load(payload.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{description} must be a mapping")
    return document


def load_hardware_identification_config(
    path: str | Path,
) -> HardwareIdentificationConfig:
    payload = Path(path).read_bytes()
    return HardwareIdentificationConfig.model_validate(
        _yaml_mapping(payload, "hardware identification config")
    )


def _execution_config(
    config: HardwareIdentificationConfig,
) -> _HardwareExecutionConfig:
    allowed = set(_HardwareExecutionConfig.model_fields)
    values = config.model_dump(
        mode="python", exclude={"enable_token", "electrical_evidence_path"}
    )
    return _HardwareExecutionConfig.model_validate(
        {name: value for name, value in values.items() if name in allowed}
    )


def expected_confirmation(
    run_id: str,
    config_sha256: str,
    manifest_sha256: str,
    electrical_evidence_sha256: str | None = None,
) -> str:
    if electrical_evidence_sha256 is None:
        return f"ENABLE {run_id} CONFIG {config_sha256} MANIFEST {manifest_sha256}"
    return (
        f"PREPARE {run_id} CONFIG {config_sha256} MANIFEST {manifest_sha256} "
        f"ELECTRICAL {electrical_evidence_sha256}"
    )


def _fresh(
    *,
    wall: datetime,
    monotonic_ns: int,
    now_wall: datetime,
    now_ns: int,
    max_age_ns: int,
    label: str,
) -> None:
    wall_age = now_wall - wall
    monotonic_age = now_ns - monotonic_ns
    if wall_age < timedelta(0) or monotonic_age < 0:
        raise ValueError(f"{label} timestamp is in the future")
    if wall_age >= timedelta(microseconds=max_age_ns / 1_000) or (
        monotonic_age >= max_age_ns
    ):
        raise ValueError(f"{label} is stale")


def _resolve_linux_usb_identity(stable_path: str) -> LinuxUsbIdentity:
    """Resolve actual tty ancestry through sysfs, independent of link naming."""

    link = Path(stable_path)
    if not link.is_symlink():
        raise ValueError("stable USB device path is not a symlink")
    resolved = link.resolve(strict=True)
    tty_device = Path("/sys/class/tty") / resolved.name / "device"
    current = tty_device.resolve(strict=True)
    interface: str | None = None
    serial: str | None = None
    for parent in (current, *current.parents):
        interface_path = parent / "bInterfaceNumber"
        serial_path = parent / "serial"
        if interface is None and interface_path.is_file():
            interface = interface_path.read_text(encoding="ascii").strip().zfill(2)
        if serial is None and serial_path.is_file():
            serial = serial_path.read_text(encoding="ascii").strip()
        if interface is not None and serial is not None:
            break
    if interface is None or serial is None:
        raise ValueError("USB serial/interface identity is unavailable in sysfs")
    return LinuxUsbIdentity(
        serial_number=serial,
        interface_number=interface,
        resolved_tty=str(resolved),
    )


def _challenge(
    config: HardwareIdentificationConfig,
    *,
    config_hash: str,
    manifest_hash: str,
    evidence_hash: str,
) -> PowerEnableChallenge:
    issued_at = datetime.now(UTC)
    issued_ns = time.monotonic_ns()
    expires_ns = issued_ns + config.power_enable_challenge_ttl_ms * 1_000_000
    provisional = PowerEnableChallenge(
        challenge_id=secrets.token_urlsafe(32),
        run_id=config.run_id,
        config_sha256=config_hash,
        manifest_sha256=manifest_hash,
        electrical_evidence_sha256=evidence_hash,
        issued_at=issued_at,
        issued_monotonic_ns=issued_ns,
        expires_monotonic_ns=expires_ns,
        challenge_sha256="0" * 64,
    )
    return provisional.model_copy(
        update={"challenge_sha256": _challenge_digest(provisional)}
    )


def prepare_hardware_identification(
    *,
    config_path: str | Path,
    manifest_path: str | Path,
    approval: HardwareApproval | None,
    attestation: HardwarePreflightAttestation,
    enable_hardware: bool,
) -> PreparedHardwareHandle:
    """Perform power-off gates and read-only device preflight, then pause."""

    # Each mutable input is read exactly once; all later work uses these bytes/objects.
    config_bytes = Path(config_path).read_bytes()
    manifest_bytes = Path(manifest_path).read_bytes()
    config = HardwareIdentificationConfig.model_validate(
        _yaml_mapping(config_bytes, "hardware identification config")
    )
    manifest = HardwareManifest.model_validate(
        _yaml_mapping(manifest_bytes, "hardware manifest")
    )
    if config.electrical_evidence_path.startswith(_PLACEHOLDER_PREFIX):
        raise ValueError("reviewed electrical evidence path is unresolved")
    evidence_bytes = Path(config.electrical_evidence_path).read_bytes()
    evidence = ElectricalSafetyEvidence.model_validate(
        _yaml_mapping(evidence_bytes, "electrical safety evidence")
    )
    electrical_source_bytes = Path(evidence.source).read_bytes()
    if _sha256_bytes(electrical_source_bytes) != evidence.source_document_sha256:
        raise ValueError("electrical evidence source document checksum mismatch")
    config_hash = _sha256_bytes(config_bytes)
    manifest_hash = _sha256_bytes(manifest_bytes)
    evidence_hash = _sha256_bytes(evidence_bytes)
    now_wall, now_ns = datetime.now(UTC), time.monotonic_ns()
    if not enable_hardware:
        raise ValueError("explicit --enable-hardware flag is required")
    token = config.enable_token.get_secret_value()
    if any(
        value.startswith(_PLACEHOLDER_PREFIX)
        for value in (
            config.run_id,
            config.approval_id,
            token,
            config.electrical_evidence_sha256,
        )
    ):
        raise ValueError("repository hardware config contains a REQUIRED placeholder")
    if approval is None:
        raise ValueError("run-specific operator approval is required")
    if (
        approval.run_id != config.run_id
        or approval.approval_id != config.approval_id
        or approval.enable_token.get_secret_value() != token
    ):
        raise ValueError("approval identity or enable token mismatch")
    if (
        approval.config_sha256 != config_hash
        or approval.manifest_sha256 != manifest_hash
        or approval.electrical_evidence_sha256 != evidence_hash
        or config.electrical_evidence_sha256 != evidence_hash
    ):
        raise ValueError("approval or electrical evidence checksum mismatch")
    expected = expected_confirmation(
        config.run_id, config_hash, manifest_hash, evidence_hash
    )
    if approval.confirmation_text != expected:
        raise ValueError("approval confirmation does not bind exact hashes")
    _fresh(
        wall=approval.approved_at,
        monotonic_ns=approval.approved_monotonic_ns,
        now_wall=now_wall,
        now_ns=now_ns,
        max_age_ns=config.safety_limits.approval_max_age_ns,
        label="operator approval",
    )
    _fresh(
        wall=attestation.observed_at,
        monotonic_ns=attestation.observed_monotonic_ns,
        now_wall=now_wall,
        now_ns=now_ns,
        max_age_ns=config.safety_limits.preflight_max_age_ns,
        label="preflight attestation",
    )
    if approval.approved_monotonic_ns < attestation.observed_monotonic_ns:
        raise ValueError("operator approval must follow the preflight attestation")
    if approval.operator_acknowledgment != _APPROVAL_ACK:
        raise ValueError("operator approval acknowledgment mismatch")
    if attestation.operator_acknowledgment != _PREFLIGHT_ACK:
        raise ValueError("preflight acknowledgment mismatch")
    if attestation.run_id != config.run_id:
        raise ValueError("preflight run identity mismatch")
    evidence_age = now_wall - evidence.reviewed_at
    if evidence_age < timedelta(0):
        raise ValueError("electrical safety evidence review is in the future")
    if evidence_age >= timedelta(days=config.electrical_review_max_age_days):
        raise ValueError("electrical safety evidence review is stale")
    if manifest_hash != config.hardware_manifest_sha256:
        raise ValueError("hardware manifest file checksum mismatch")
    if manifest.canonical_sha256 != config.hardware_manifest_canonical_sha256:
        raise ValueError("hardware manifest canonical checksum mismatch")
    if manifest.calibration_sha256 != config.calibration_sha256:
        raise ValueError("calibration checksum mismatch")
    if config.stable_device_path != manifest.controller.command_device_path:
        raise ValueError("stable device path mismatch")
    identity = _resolve_linux_usb_identity(config.stable_device_path)
    if (
        identity.serial_number != config.expected_controller_serial
        or identity.interface_number != config.expected_usb_interface
    ):
        raise ValueError("actual USB identity does not match reviewed identity")

    supervisor = SafetySupervisor(
        manifest=manifest, limits=config.safety_limits, clock=time.monotonic_ns
    )
    adapter = MaestroAdapter(
        manifest=manifest,
        stable_device_path=config.stable_device_path,
        expected_controller_serial=config.expected_controller_serial,
        required_enable_token=token,
        clock=time.monotonic_ns,
        permit_verifier=supervisor.actuation_permit_verifier,
        settle_timeout_ns=config.controller_settle_ms * 1_000_000,
    )
    try:
        adapter.open(token)
        snapshot = adapter.read_only_preflight(config.actuator_names)
        home_ok = all(
            abs(snapshot.positions_qus[name] - manifest.actuator(name).home_qus)
            <= config.home_tolerance_qus
            for name in config.actuator_names
        )
        preflight = PreflightEvidence(
            run_id=config.run_id,
            hardware_id=manifest.hardware_id,
            calibration_sha256=manifest.calibration_sha256,
            controller_serial=identity.serial_number,
            requirement_results=attestation.requirement_results,
            competing_process_detected=False,
            controller_error_codes=(
                (snapshot.controller_error_register,)
                if snapshot.controller_error_register
                else ()
            ),
            home_verified=home_ok,
            observed_monotonic_ns=snapshot.observed_monotonic_ns,
        )
        result = supervisor.preflight(preflight)
        if not result.accepted or result.state is not RunState.PREFLIGHT:
            raise RuntimeError("device-reading preflight rejected")
        challenge = _challenge(
            config,
            config_hash=config_hash,
            manifest_hash=manifest_hash,
            evidence_hash=evidence_hash,
        )
        state = _PreparedState(
            challenge=challenge,
            config=config,
            config_bytes=config_bytes,
            execution_config=_execution_config(config),
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            electrical_evidence=evidence,
            electrical_evidence_bytes=evidence_bytes,
            electrical_source_bytes=electrical_source_bytes,
            approval=approval,
            preflight=preflight,
            read_only_snapshot=snapshot,
            usb_identity=identity,
            supervisor=supervisor,
            adapter=adapter,
        )
        issuance_token = secrets.token_urlsafe(48)
        with _registry_lock:
            while issuance_token in _prepared_registry:
                issuance_token = secrets.token_urlsafe(48)
            _prepared_registry[issuance_token] = state
        timeout_seconds = max(
            0.0,
            (challenge.expires_monotonic_ns - time.monotonic_ns()) / 1_000_000_000,
        )
        timer = threading.Timer(
            timeout_seconds, _expire_or_abandon, args=(issuance_token,)
        )
        timer.daemon = True
        state.timer = timer
        timer.start()
        handle = PreparedHardwareHandle(
            challenge=challenge,
            issuance_token=SecretStr(issuance_token),
        )
        weakref.finalize(handle, _expire_or_abandon, issuance_token)
        return handle
    except BaseException:
        try:
            supervisor.revoke_external_authority("hardware preparation failed")
        except Exception:
            pass
        try:
            adapter.close()
        except Exception:
            pass
        raise


def _validate_power_confirmation(
    challenge: PowerEnableChallenge,
    confirmation: PowerEnableConfirmation,
    *,
    now_wall: datetime,
    now_ns: int,
) -> None:
    bindings = (
        confirmation.run_id == challenge.run_id
        and confirmation.challenge_id == challenge.challenge_id
        and confirmation.config_sha256 == challenge.config_sha256
        and confirmation.manifest_sha256 == challenge.manifest_sha256
        and confirmation.electrical_evidence_sha256
        == challenge.electrical_evidence_sha256
        and confirmation.challenge_sha256 == challenge.challenge_sha256
    )
    if not bindings:
        raise ValueError("power confirmation is not bound to the prepared challenge")
    if confirmation.confirmed_monotonic_ns < challenge.issued_monotonic_ns:
        raise ValueError("power confirmation must occur after preparation")
    if confirmation.confirmed_monotonic_ns >= challenge.expires_monotonic_ns:
        raise ValueError("power confirmation is stale")
    if now_ns >= challenge.expires_monotonic_ns:
        raise ValueError("prepared power challenge expired")
    if confirmation.confirmed_at < challenge.issued_at:
        raise ValueError("power confirmation wall time predates preparation")
    if confirmation.confirmed_at > now_wall:
        raise ValueError("power confirmation timestamp is in the future")
    if confirmation.operator_acknowledgment != _POWER_ACK:
        raise ValueError("power enable acknowledgment mismatch")


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
    def provenance(self) -> IdentificationObserverProvenance:
        return self._observer.provenance

    def observe(self, *, run_id: str, step_id: str):  # type: ignore[no-untyped-def]
        self._watchdog.pet()
        result = self._observer.observe(run_id=run_id, step_id=step_id)
        self._watchdog.pet()
        return result


def _artifact_record(path: str, payload: bytes) -> ArtifactRecord:
    return ArtifactRecord(
        path=path, sha256=_sha256_bytes(payload), size_bytes=len(payload)
    )


def _git_revision() -> str | None:
    try:
        value = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return value or None


def _hardware_provenance(
    state: _PreparedState,
    confirmation: PowerEnableConfirmation,
) -> HardwareIdentificationProvenance:
    config = state._config
    evidence = state._electrical_evidence
    snapshot = state._read_only_snapshot
    approval_values = state._approval.model_dump(
        mode="python", exclude={"enable_token", "confirmation_text"}
    )
    return HardwareIdentificationProvenance(
        raw_config_sha256=state.challenge.config_sha256,
        hardware_approval=HardwareApprovalProvenance.model_validate(
            approval_values
        ),
        power_challenge=PowerChallengeProvenance.model_validate(
            state.challenge.model_dump(mode="python")
        ),
        power_confirmation=PowerConfirmationProvenance.model_validate(
            confirmation.model_dump(mode="python")
        ),
        electrical_safety=ElectricalSafetyProvenance(
            evidence_id=evidence.evidence_id,
            evidence_sha256=state.challenge.electrical_evidence_sha256,
            source=evidence.source,
            source_document_sha256=evidence.source_document_sha256,
            reviewed_at=evidence.reviewed_at,
            reviewer=evidence.reviewer,
            supply_voltage_v=evidence.supply_voltage_v,
            current_limit_a=evidence.current_limit_a,
            scope=evidence.scope,
        ),
        usb_identity=UsbIdentityProvenance(
            serial_number=state._usb_identity.serial_number,
            interface_number=state._usb_identity.interface_number,
            resolved_tty=state._usb_identity.resolved_tty,
            stable_device_path=config.stable_device_path,
        ),
        read_only_preflight=ReadOnlyControllerPreflightProvenance(
            controller_error_register=snapshot.controller_error_register,
            positions=tuple(
                ControllerPreflightPosition(
                    actuator_name=name,
                    observed_qus=observed,
                    expected_home_qus=state._manifest.actuator(name).home_qus,
                    tolerance_qus=config.home_tolerance_qus,
                )
                for name, observed in sorted(snapshot.positions_qus.items())
            ),
            observed_monotonic_ns=snapshot.observed_monotonic_ns,
            issued_set_target=False,
        ),
        independent_watchdog=IndependentWatchdogProvenance(
            implementation="process-local-os-monotonic-thread/v1",
            clock="time.monotonic",
            timeout_ms=config.independent_watchdog_ms,
            actions=("revoke-permits", "close-adapter"),
            survives_process_death=False,
        ),
    )


def _publish_unexpected_failure(
    prepared: _PreparedState,
    *,
    confirmation: PowerEnableConfirmation,
    output_dir: Path,
    error: BaseException,
) -> None:
    config = prepared._execution_config
    provenance = _hardware_provenance(prepared, confirmation)
    provenance_payload = json.dumps(
        provenance.model_dump(mode="json"), sort_keys=True, indent=2
    ).encode()
    failure_payload = json.dumps(
        {
            "schema_version": "hardware-failure/v1",
            "run_id": config.run_id,
            "error_type": type(error).__name__,
            "config_sha256": prepared.challenge.config_sha256,
            "manifest_sha256": prepared.challenge.manifest_sha256,
            "electrical_evidence_sha256": (
                prepared.challenge.electrical_evidence_sha256
            ),
            "challenge_sha256": prepared.challenge.challenge_sha256,
            "hardware_provenance_sha256": _sha256_bytes(provenance_payload),
        },
        sort_keys=True,
        indent=2,
    ).encode()
    artifacts = {
        "hardware-failure.json": _artifact_record(
            "hardware-failure.json", failure_payload
        ),
        "hardware-provenance.json": _artifact_record(
            "hardware-provenance.json", provenance_payload
        ),
    }
    lock_path = Path("uv.lock")
    manifest = ArtifactManifest(
        schema_version="artifact-manifest/v1",
        run_kind=RunKind.ACTUATOR_IDENTIFICATION,
        run_id=config.run_id,
        status=RunStatus.ABORTED,
        started_at=prepared._started_at,
        ended_at=datetime.now(UTC),
        observation_count=0,
        config=config.model_dump(mode="json"),
        artifacts=artifacts,
        git_revision=_git_revision(),
        dependency_lock_path="uv.lock" if lock_path.is_file() else None,
        dependency_lock_sha256=sha256_path(lock_path) if lock_path.is_file() else None,
        python_version=platform.python_version(),
        platform_system=platform.system() or "unknown",
        platform_release=platform.release() or "unknown",
        platform_machine=platform.machine() or "unknown",
        camera_settings=NegotiatedCameraSettings.unavailable(),
        aborted_reason="hardware identification aborted by unexpected process exit",
        failure=FailureRecord(
            category=FailureCategory.INTERRUPTED,
            error_type=type(error).__name__,
        ),
        conclusion=None,
        identification_metadata=IdentificationRunMetadata(
            adapter_identity=prepared._adapter.identity,
            observer=None,
            expected_observer=config.observer,
            safety_limits=config.safety_limits,
            preflight=prepared._preflight,
            approval=prepared._supervisor.operator_approval,
            hardware_manifest_path=config.hardware_manifest_path,
            hardware_manifest_sha256=prepared.challenge.manifest_sha256,
            hardware_manifest_canonical_sha256=prepared._manifest.canonical_sha256,
            calibration_sha256=prepared._manifest.calibration_sha256,
            config_sha256=prepared.challenge.config_sha256,
            hardware_provenance=provenance,
        ),
    )
    files = {
        "hardware-failure.json": failure_payload,
        "hardware-provenance.json": provenance_payload,
        "manifest.json": json.dumps(
            manifest.model_dump(mode="json"), sort_keys=True, indent=2
        ).encode(),
    }
    publish_generation(output_dir.parent.resolve(), output_dir.name, files)


def execute_prepared_hardware_identification(
    *,
    prepared: PreparedHardwareHandle,
    observer: IdentificationObserver,
    output_dir: Path,
    confirmation: PowerEnableConfirmation,
) -> ArtifactManifest:
    """Consume a prepared session only after a new, bound power-on confirmation."""

    state = _consume_handle(prepared)
    config = state._execution_config
    supervisor = state._supervisor
    adapter = state._adapter
    watchdog = IndependentHardwareWatchdog(
        timeout_seconds=config.independent_watchdog_ms / 1_000,
        revoke=lambda: supervisor.revoke_external_authority(
            "independent OS-monotonic watchdog expired"
        ),
        close=adapter.close,
    )
    try:
        _validate_power_confirmation(
            state.challenge,
            confirmation,
            now_wall=datetime.now(UTC),
            now_ns=time.monotonic_ns(),
        )
        armed = supervisor.arm(
            OperatorApproval(
                approval_id=state._approval.approval_id,
                run_id=config.run_id,
                confirmed_monotonic_ns=confirmation.confirmed_monotonic_ns,
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
            retained_manifest=state._manifest,
            retained_manifest_sha256=state.challenge.manifest_sha256,
            retained_config_sha256=state.challenge.config_sha256,
            hardware_provenance=_hardware_provenance(state, confirmation),
        )
    except BaseException as error:
        try:
            supervisor.revoke_external_authority(
                "hardware execution failed; physical command state may be unknown"
            )
        except Exception:
            pass
        try:
            _publish_unexpected_failure(
                state,
                confirmation=confirmation,
                output_dir=output_dir,
                error=error,
            )
        except Exception:
            pass
        raise
    finally:
        try:
            watchdog.stop()
        except Exception:
            pass
        try:
            adapter.close()
        except Exception:
            pass


def cancel_prepared_hardware_identification(
    prepared: PreparedHardwareHandle,
) -> None:
    state = _consume_handle(prepared)
    _close_state(state, "prepared hardware run cancelled before power enable")
