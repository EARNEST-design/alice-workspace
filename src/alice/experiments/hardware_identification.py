"""Three-stage, capability-gated Maestro identification composition."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Never, SupportsIndex

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
from alice.experiments.artifact_store import (
    atomic_write_bytes,
    fsync_directory,
    publish_generation,
    sha256_path,
)
from alice.experiments.manifest import (
    ArtifactManifest,
    ArtifactRecord,
    ControllerPreflightPosition,
    ElectricalSafetyProvenance,
    FailureCategory,
    FailureRecord,
    HardwareApprovalProvenance,
    HardwareIdentificationProvenance,
    HardwareShutdownProvenance,
    IdentificationObserverProvenance,
    IdentificationRunMetadata,
    IndependentWatchdogProvenance,
    NegotiatedCameraSettings,
    PowerChallengeProvenance,
    PowerConfirmationProvenance,
    PowerRemovalProvenance,
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
from alice.perception.identification_observer import (
    C525_CAMERA_ID,
    C525_DEVICE,
    ProductionIdentificationObserver,
)
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
_POWER_REMOVAL_ACK = "I CONFIRM MASTER SERVO POWER IS OFF"


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
    power_removal_confirmation_ttl_ms: Annotated[int, Field(gt=0)]
    safety_limits: SafetyLimits
    camera_device: NonEmptyString
    detector_model_path: NonEmptyString

    @model_validator(mode="after")
    def validate_hardware_identity(self) -> HardwareIdentificationConfig:
        if not self.stable_device_path.endswith("00037376-if00"):
            raise ValueError("exact reviewed interface-00 stable path is required")
        if self.expected_controller_serial != "00037376":
            raise ValueError("reviewed controller serial is required")
        if self.independent_watchdog_ms >= self.step_timeout_ms:
            raise ValueError("independent watchdog must precede the step timeout")
        if (
            self.camera_device != C525_DEVICE
            or self.observer.camera_id != C525_CAMERA_ID
        ):
            raise ValueError("exact reviewed C525 observer identity is required")
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
    power_removal_confirmation_ttl_ms: Annotated[int, Field(gt=0)]
    safety_limits: SafetyLimits
    camera_device: NonEmptyString
    detector_model_path: NonEmptyString


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
    operator_acknowledgment: Literal["I CONFIRM PREFLIGHT WITH MASTER SERVO POWER OFF"]
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
    output_identity_sha256: Sha256Hex | None = None
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
    output_identity_sha256: Sha256Hex | None = None
    challenge_sha256: Sha256Hex
    confirmed_at: AwareDatetime
    confirmed_monotonic_ns: Annotated[int, Field(ge=0)]
    source: NonEmptyString
    operator_acknowledgment: Literal[
        "I CONFIRM MASTER SERVO POWER IS ON AND POWER REMOVAL IS READY"
    ]


class PowerRemovalConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: NonEmptyString
    challenge_id: NonEmptyString
    config_sha256: Sha256Hex
    manifest_sha256: Sha256Hex
    draft_sha256: Sha256Hex
    confirmed_at: AwareDatetime
    confirmed_monotonic_ns: Annotated[int, Field(ge=0)]
    source: NonEmptyString
    operator_acknowledgment: Literal["I CONFIRM MASTER SERVO POWER IS OFF"]


class FailedRunPowerRemovalConfirmation(BaseModel):
    """Fresh power-OFF fact for an execution that could not reach staging."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: NonEmptyString
    challenge_id: NonEmptyString
    config_sha256: Sha256Hex
    manifest_sha256: Sha256Hex
    output_identity_sha256: Sha256Hex
    confirmed_at: AwareDatetime
    confirmed_monotonic_ns: Annotated[int, Field(ge=0)]
    source: NonEmptyString
    operator_acknowledgment: Literal["I CONFIRM MASTER SERVO POWER IS OFF"]


class ShutdownFinalizationManifest(BaseModel):
    """Immutable linkage from a shutdown fact to an aborted run generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["shutdown-finalization-manifest/v1"]
    generation_id: NonEmptyString
    run_id: NonEmptyString
    outcome: Literal["power_removed_confirmed", "power_removal_unconfirmed"]
    generated_at: AwareDatetime
    generated_monotonic_ns: Annotated[int, Field(ge=0)]
    challenge_id: NonEmptyString
    config_sha256: Sha256Hex
    execution_config_sha256: Sha256Hex
    manifest_sha256: Sha256Hex
    output_identity_sha256: Sha256Hex
    aborted_run_manifest: ArtifactRecord
    shutdown_evidence: ArtifactRecord

    @model_validator(mode="after")
    def validate_logical_paths(self) -> ShutdownFinalizationManifest:
        if self.aborted_run_manifest.path != "manifest.json":
            raise ValueError("aborted run manifest path must be manifest.json")
        if self.shutdown_evidence.path != "shutdown-evidence.json":
            raise ValueError("shutdown evidence path must be shutdown-evidence.json")
        return self


def _shutdown_finalization(
    *,
    output_dir: Path,
    outcome: Literal["power_removed_confirmed", "power_removal_unconfirmed"],
    evidence: Mapping[str, object],
) -> Path:
    original_manifest = output_dir / "manifest.json"
    if not original_manifest.is_file():
        raise FileNotFoundError("aborted run manifest is unavailable")
    aborted = ArtifactManifest.model_validate_json(original_manifest.read_bytes())
    metadata = aborted.identification_metadata
    provenance = metadata.hardware_provenance if metadata is not None else None
    if (
        aborted.status is not RunStatus.ABORTED
        or aborted.run_kind is not RunKind.ACTUATOR_IDENTIFICATION
        or metadata is None
        or not metadata.adapter_identity.hardware_capable
        or provenance is None
    ):
        raise ValueError("shutdown finalization requires an aborted hardware run")
    challenge = provenance.power_challenge
    expected = {
        "run_id": aborted.run_id,
        "challenge_id": challenge.challenge_id,
        "config_sha256": provenance.approved_raw_config_sha256,
        "manifest_sha256": metadata.hardware_manifest_sha256,
        "output_identity_sha256": challenge.output_identity_sha256,
    }
    if any(evidence.get(key) != value for key, value in expected.items()):
        raise ValueError("shutdown evidence does not match aborted hardware provenance")
    evidence_payload = json.dumps(evidence, sort_keys=True, indent=2).encode()
    generation_id = f"shutdown-{secrets.token_hex(16)}"
    manifest = ShutdownFinalizationManifest(
        schema_version="shutdown-finalization-manifest/v1",
        generation_id=generation_id,
        run_id=str(evidence["run_id"]),
        outcome=outcome,
        generated_at=datetime.now(UTC),
        generated_monotonic_ns=time.monotonic_ns(),
        challenge_id=str(evidence["challenge_id"]),
        config_sha256=str(evidence["config_sha256"]),
        execution_config_sha256=provenance.execution_config_sha256,
        manifest_sha256=str(evidence["manifest_sha256"]),
        output_identity_sha256=str(evidence["output_identity_sha256"]),
        aborted_run_manifest=ArtifactRecord(
            path="manifest.json",
            sha256=sha256_path(original_manifest),
            size_bytes=original_manifest.stat().st_size,
        ),
        shutdown_evidence=_artifact_record("shutdown-evidence.json", evidence_payload),
    )
    finalization_payload = json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True, indent=2
    ).encode()
    return publish_generation(
        output_dir.parent / f"{output_dir.name}.shutdown" / "generations",
        generation_id,
        {
            "shutdown-evidence.json": evidence_payload,
            "shutdown-finalization.json": finalization_payload,
        },
    )


def record_failed_hardware_power_removal(
    *, output_dir: Path, confirmation: FailedRunPowerRemovalConfirmation
) -> Path:
    """Durably record shutdown after a failed/aborted hardware execution."""

    evidence = {
        "schema_version": "aborted-hardware-shutdown/v1",
        "power_removal_unconfirmed": False,
        "servo_power_removed": True,
        **confirmation.model_dump(mode="json"),
    }
    return _shutdown_finalization(
        output_dir=output_dir,
        outcome="power_removed_confirmed",
        evidence=evidence,
    )


def record_failed_hardware_power_removal_unconfirmed(
    *, output_dir: Path, challenge: PowerEnableChallenge
) -> Path:
    """Durably mark a failed run when the operator did not acknowledge power OFF."""

    if challenge.output_identity_sha256 is None:
        raise ValueError("prepared output identity was not bound")
    return _shutdown_finalization(
        output_dir=output_dir,
        outcome="power_removal_unconfirmed",
        evidence={
            "schema_version": "aborted-hardware-shutdown/v1",
            "run_id": challenge.run_id,
            "challenge_id": challenge.challenge_id,
            "config_sha256": challenge.config_sha256,
            "manifest_sha256": challenge.manifest_sha256,
            "output_identity_sha256": challenge.output_identity_sha256,
            "recorded_at": datetime.now(UTC).isoformat(),
            "recorded_monotonic_ns": time.monotonic_ns(),
            "power_removal_unconfirmed": True,
            "servo_power_removed": False,
        },
    )


def verify_shutdown_finalization(
    generation_dir: Path, *, aborted_output_dir: Path
) -> ShutdownFinalizationManifest:
    """Verify both immutable shutdown evidence and its exact aborted-run linkage."""

    manifest_path = generation_dir / "shutdown-finalization.json"
    evidence_path = generation_dir / "shutdown-evidence.json"
    manifest = ShutdownFinalizationManifest.model_validate_json(
        manifest_path.read_bytes()
    )
    if generation_dir.name != manifest.generation_id:
        raise ValueError("shutdown generation identity mismatch")
    if (
        sha256_path(evidence_path) != manifest.shutdown_evidence.sha256
        or evidence_path.stat().st_size != manifest.shutdown_evidence.size_bytes
    ):
        raise ValueError("shutdown evidence checksum or size mismatch")
    aborted_manifest = aborted_output_dir / manifest.aborted_run_manifest.path
    if (
        sha256_path(aborted_manifest) != manifest.aborted_run_manifest.sha256
        or aborted_manifest.stat().st_size != manifest.aborted_run_manifest.size_bytes
    ):
        raise ValueError("aborted run manifest checksum or size mismatch")
    aborted = ArtifactManifest.model_validate_json(aborted_manifest.read_bytes())
    metadata = aborted.identification_metadata
    provenance = metadata.hardware_provenance if metadata is not None else None
    if (
        aborted.status is not RunStatus.ABORTED
        or aborted.run_kind is not RunKind.ACTUATOR_IDENTIFICATION
        or metadata is None
        or not metadata.adapter_identity.hardware_capable
        or provenance is None
    ):
        raise ValueError("linked manifest is not an aborted hardware run")
    challenge = provenance.power_challenge
    semantic_bindings = {
        "run_id": aborted.run_id,
        "challenge_id": challenge.challenge_id,
        "config_sha256": provenance.approved_raw_config_sha256,
        "execution_config_sha256": provenance.execution_config_sha256,
        "manifest_sha256": metadata.hardware_manifest_sha256,
        "output_identity_sha256": challenge.output_identity_sha256,
    }
    if any(getattr(manifest, key) != value for key, value in semantic_bindings.items()):
        raise ValueError("shutdown finalization does not match hardware provenance")
    evidence = json.loads(evidence_path.read_bytes())
    for field in (
        "run_id",
        "challenge_id",
        "config_sha256",
        "manifest_sha256",
        "output_identity_sha256",
    ):
        if evidence.get(field) != getattr(manifest, field):
            raise ValueError(f"shutdown evidence linkage mismatch: {field}")
    expected_removed = manifest.outcome == "power_removed_confirmed"
    if evidence.get("servo_power_removed") is not expected_removed:
        raise ValueError("shutdown outcome does not match servo_power_removed evidence")
    return manifest


class HardwareRunDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["hardware-run-draft/v1"]
    status: Literal["pending_power_removal"]
    run_id: NonEmptyString
    challenge_id: NonEmptyString
    config_sha256: Sha256Hex
    manifest_sha256: Sha256Hex
    final_home_verified: Literal[True]
    watchdog_stopped: Literal[True]
    adapter_closed: Literal[True]
    observer_closed: Literal[True]
    motion_ended_monotonic_ns: Annotated[int, Field(ge=0)]
    cleanup_completed_monotonic_ns: Annotated[int, Field(ge=0)]
    expires_monotonic_ns: Annotated[int, Field(gt=0)]
    output_identity_sha256: Sha256Hex
    draft_sha256: Sha256Hex


@dataclass(frozen=True, slots=True, weakref_slot=True)
class PendingPowerRemovalHandle:
    draft: HardwareRunDraft
    issuance_token: SecretStr

    def __copy__(self) -> Never:
        raise TypeError("pending power-removal handles cannot be copied")

    def __deepcopy__(self, memo: object) -> Never:
        raise TypeError("pending power-removal handles cannot be copied")

    def __reduce__(self) -> Never:
        raise TypeError("pending power-removal handles cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("pending power-removal handles cannot be serialized")


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
            if thread.is_alive():
                raise RuntimeError("independent watchdog thread did not stop")

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
        "handle_ref",
        "issuing_pid",
        "raw_fd",
        "challenge",
        "reservation",
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
        raw_fd: int,
    ) -> None:
        self.challenge = challenge
        self.reservation: _OutputReservation | None = None
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
        self.raw_fd = raw_fd
        self._started_at = datetime.now(UTC)
        self.issuing_pid = os.getpid()
        self.handle_ref: weakref.ReferenceType[PreparedHardwareHandle] | None = None
        self.timer: threading.Timer | None = None


@dataclass(frozen=True, slots=True, weakref_slot=True)
class PreparedHardwareHandle:
    """Display-only challenge and opaque same-process issuance capability."""

    challenge: PowerEnableChallenge
    issuance_token: SecretStr

    def __copy__(self) -> Never:
        raise TypeError("prepared hardware handles cannot be copied")

    def __deepcopy__(self, memo: object) -> Never:
        raise TypeError("prepared hardware handles cannot be copied")

    def __reduce__(self) -> Never:
        raise TypeError("prepared hardware handles cannot be serialized")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("prepared hardware handles cannot be serialized")


_registry_lock = threading.Lock()
_prepared_registry: dict[str, _PreparedState] = {}


class _PendingState:
    def __init__(
        self,
        *,
        prepared: _PreparedState,
        draft: HardwareRunDraft,
        draft_directory: Path,
        output_dir: Path,
        manifest: ArtifactManifest,
        reservation: _OutputReservation,
    ) -> None:
        self.prepared = prepared
        self.draft = draft
        self.draft_directory = draft_directory
        self.output_dir = output_dir
        self.manifest = manifest
        self.reservation = reservation
        self.issuing_pid = os.getpid()
        self.handle_ref: weakref.ReferenceType[PendingPowerRemovalHandle] | None = None
        self.timer: threading.Timer | None = None


_pending_registry: dict[str, _PendingState] = {}


@dataclass(frozen=True, slots=True)
class _OutputReservation:
    output_dir: Path
    marker: Path
    identity_sha256: str


def _reserve_output(output_dir: Path, run_id: str) -> _OutputReservation:
    output = output_dir.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"output destination already exists: {output.name}")
    identity = _sha256_bytes(f"hardware-output/v1\0{run_id}\0{output}".encode())
    marker = output.parent / f".alice-output-reservation-{identity[:24]}"
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(
                json.dumps(
                    {
                        "schema_version": "hardware-output-reservation/v1",
                        "run_id": run_id,
                        "output_identity_sha256": identity,
                    },
                    sort_keys=True,
                ).encode()
            )
            handle.flush()
            os.fsync(handle.fileno())
        fsync_directory(output.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        output.mkdir(mode=0o700)
        fsync_directory(output.parent)
    except BaseException:
        marker.unlink(missing_ok=True)
        raise
    return _OutputReservation(output, marker, identity)


def _release_reservation(
    reservation: _OutputReservation, *, remove_empty_output: bool = False
) -> None:
    if remove_empty_output and reservation.output_dir.is_dir():
        try:
            reservation.output_dir.rmdir()
        except OSError:
            pass
    reservation.marker.unlink(missing_ok=True)
    fsync_directory(reservation.output_dir.parent)


def _publish_reserved_generation(
    reservation: _OutputReservation, files: Mapping[str, bytes]
) -> None:
    if (
        not reservation.marker.is_file()
        or not reservation.output_dir.is_dir()
        or any(reservation.output_dir.iterdir())
    ):
        raise FileExistsError("reserved output identity is unavailable")
    stage_id = f"reserved-{secrets.token_hex(16)}"
    stage = publish_generation(reservation.output_dir.parent, stage_id, files)
    try:
        os.replace(stage, reservation.output_dir)
        fsync_directory(reservation.output_dir.parent)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    _release_reservation(reservation)


def _after_fork_child() -> None:
    global _prepared_registry, _pending_registry
    inherited, _prepared_registry = _prepared_registry, {}
    for token in inherited:
        try:
            os.close(inherited[token].raw_fd)
        except OSError:
            pass
    inherited.clear()
    _pending_registry = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        after_in_child=_after_fork_child,
    )


def _close_state(state: _PreparedState, detail: str) -> None:
    try:
        state._supervisor.revoke_external_authority(detail)
    except Exception:
        pass
    try:
        state._adapter.close()
    except Exception:
        pass
    if state.reservation is not None and state.reservation.marker.exists():
        _release_reservation(state.reservation, remove_empty_output=True)


def _remove_state(token: str) -> _PreparedState | None:
    if token not in _prepared_registry:
        return None
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
    authority_ok = (
        state.issuing_pid == os.getpid()
        and state.handle_ref is not None
        and state.handle_ref() is handle
    )
    if not authority_ok:
        _close_state(state, "prepared hardware authority identity failed")
        raise ValueError("prepared hardware handle authority is invalid")
    challenge_ok = (
        handle.challenge == state.challenge
        and _challenge_digest(handle.challenge) == handle.challenge.challenge_sha256
        and _challenge_digest(state.challenge) == state.challenge.challenge_sha256
    )
    if not challenge_ok:
        _close_state(state, "prepared hardware challenge integrity failed")
        raise ValueError("prepared hardware challenge was mutated or forged")
    return state


def _issue_prepared_handle(state: _PreparedState) -> PreparedHardwareHandle:
    with _registry_lock:
        issuance_token = secrets.token_urlsafe(48)
        while issuance_token in _prepared_registry:
            issuance_token = secrets.token_urlsafe(48)
        handle = PreparedHardwareHandle(
            challenge=state.challenge,
            issuance_token=SecretStr(issuance_token),
        )
        state.handle_ref = weakref.ref(handle)
        _prepared_registry[issuance_token] = state
    timeout_seconds = max(
        0.0,
        (state.challenge.expires_monotonic_ns - time.monotonic_ns()) / 1_000_000_000,
    )
    timer = threading.Timer(timeout_seconds, _expire_or_abandon, args=(issuance_token,))
    timer.daemon = True
    state.timer = timer
    timer.start()
    weakref.finalize(handle, _expire_or_abandon, issuance_token)
    return handle


def bind_prepared_hardware_output(
    *, prepared: PreparedHardwareHandle, output_dir: Path
) -> PreparedHardwareHandle:
    """Reserve and cryptographically bind the destination while power is OFF."""

    state = _consume_handle(prepared)
    try:
        reservation = _reserve_output(output_dir, state._execution_config.run_id)
        state.reservation = reservation
        provisional = state.challenge.model_copy(
            update={
                "output_identity_sha256": reservation.identity_sha256,
                "challenge_sha256": "0" * 64,
            }
        )
        state.challenge = provisional.model_copy(
            update={"challenge_sha256": _challenge_digest(provisional)}
        )
        return _issue_prepared_handle(state)
    except BaseException:
        _close_state(state, "output reservation failed before power enable")
        raise


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
        raw_fd = adapter.fileno()
        if type(raw_fd) is not int or raw_fd < 0:
            raise RuntimeError(
                "opened transport raw OS file descriptor must be a nonnegative integer"
            )
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
            raw_fd=raw_fd,
        )
        return _issue_prepared_handle(state)
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
        and confirmation.output_identity_sha256 == challenge.output_identity_sha256
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
    execution_config_sha256 = _sha256_bytes(
        json.dumps(
            state._execution_config.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    return HardwareIdentificationProvenance(
        approved_raw_config_sha256=state.challenge.config_sha256,
        execution_config_sha256=execution_config_sha256,
        hardware_approval=HardwareApprovalProvenance.model_validate(approval_values),
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
    reservation: _OutputReservation | None = None,
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
            "approved_raw_config_sha256": prepared.challenge.config_sha256,
            "execution_config_sha256": provenance.execution_config_sha256,
            "manifest_sha256": prepared.challenge.manifest_sha256,
            "electrical_evidence_sha256": (
                prepared.challenge.electrical_evidence_sha256
            ),
            "challenge_sha256": prepared.challenge.challenge_sha256,
            "hardware_provenance_sha256": _sha256_bytes(provenance_payload),
            "power_removal_required": True,
            "completion_eligible": False,
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
            config_sha256=provenance.execution_config_sha256,
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
    if reservation is None:
        publish_generation(output_dir.parent.resolve(), output_dir.name, files)
    else:
        _publish_reserved_generation(reservation, files)


def _draft_digest(draft: HardwareRunDraft) -> str:
    payload = draft.model_dump(mode="json", exclude={"draft_sha256"})
    return _sha256_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )


def _final_home_evidence(directory: Path) -> int:
    records = [
        json.loads(line)
        for line in (directory / "transitions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    accepted = [
        record
        for record in records
        if record.get("operation") == "home-verified" and record.get("accepted") is True
    ]
    if not accepted:
        raise RuntimeError("motion did not end at independently verified Home")
    final_home = accepted[-1]
    later = records[records.index(final_home) + 1 :]
    if any(
        record.get("step_id") != final_home.get("step_id")
        or record.get("operation") != "complete-run"
        or record.get("accepted") is not True
        for record in later
    ):
        raise RuntimeError("motion occurred or completion failed after final Home")
    return int(final_home["monotonic_ns"])


def execute_prepared_hardware_identification(
    *,
    prepared: PreparedHardwareHandle,
    output_dir: Path,
    confirmation: PowerEnableConfirmation,
) -> PendingPowerRemovalHandle:
    """Execute through trusted perception, then stage evidence pending power OFF."""

    state = _consume_handle(prepared)
    config = state._execution_config
    supervisor = state._supervisor
    adapter = state._adapter
    observer: ProductionIdentificationObserver | None = None
    draft_root: Path | None = None
    reservation = state.reservation
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
        if reservation is None:
            # Compatibility for the advanced low-level API. The installed trusted
            # CLI always binds while power is OFF before requesting POWER_ON.
            reservation = _reserve_output(output_dir, config.run_id)
        elif reservation.output_dir != output_dir.absolute():
            raise ValueError("output differs from the pre-power reserved destination")
        observer = ProductionIdentificationObserver.open(
            camera_device=config.camera_device,
            model_path=Path(config.detector_model_path),
            expected_model_sha256=config.observer.detector_model_sha256,
            width=int(config.observer.camera_settings.width.value or 0),
            height=int(config.observer.camera_settings.height.value or 0),
            fps=float(config.observer.camera_settings.fps.value or 0),
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
        draft_root = Path(
            tempfile.mkdtemp(
                prefix=f".{output_dir.name}.pending-", dir=output_dir.parent
            )
        )
        draft_directory = draft_root / "run"
        watchdog.start()
        manifest = _run_identification_core(
            config=config,
            observer=_WatchedObserver(observer, watchdog),
            supervisor=supervisor,
            adapter=_WatchedAdapter(adapter, watchdog),
            output_dir=draft_directory,
            clock=time.monotonic_ns,
            sleeper=time.sleep,
            retained_manifest=state._manifest,
            retained_manifest_sha256=state.challenge.manifest_sha256,
            hardware_provenance=_hardware_provenance(state, confirmation),
            stage_hardware_completion=True,
        )
        if manifest.status is RunStatus.ABORTED:
            files = {
                str(path.relative_to(draft_directory)): path.read_bytes()
                for path in draft_directory.rglob("*")
                if path.is_file()
            }
            _publish_reserved_generation(reservation, files)
            raise RuntimeError("hardware motion run aborted before shutdown gate")
        if manifest.status is not RunStatus.STAGED:
            raise RuntimeError("hardware motion run did not produce staged evidence")
        motion_ended_ns = _final_home_evidence(draft_directory)
        watchdog.stop()
        adapter.close()
        observer.close()
        cleanup_ns = time.monotonic_ns()
        provisional = HardwareRunDraft(
            schema_version="hardware-run-draft/v1",
            status="pending_power_removal",
            run_id=config.run_id,
            challenge_id=state.challenge.challenge_id,
            config_sha256=state.challenge.config_sha256,
            manifest_sha256=state.challenge.manifest_sha256,
            final_home_verified=True,
            watchdog_stopped=True,
            adapter_closed=True,
            observer_closed=True,
            motion_ended_monotonic_ns=motion_ended_ns,
            cleanup_completed_monotonic_ns=cleanup_ns,
            expires_monotonic_ns=(
                cleanup_ns + config.power_removal_confirmation_ttl_ms * 1_000_000
            ),
            output_identity_sha256=reservation.identity_sha256,
            draft_sha256="0" * 64,
        )
        draft = provisional.model_copy(
            update={"draft_sha256": _draft_digest(provisional)}
        )
        atomic_write_bytes(
            draft_directory / "draft.json",
            json.dumps(
                draft.model_dump(mode="json"), sort_keys=True, indent=2
            ).encode(),
        )
        with _registry_lock:
            token = secrets.token_urlsafe(48)
            while token in _pending_registry:
                token = secrets.token_urlsafe(48)
            handle = PendingPowerRemovalHandle(
                draft=draft, issuance_token=SecretStr(token)
            )
            pending_state = _PendingState(
                prepared=state,
                draft=draft,
                draft_directory=draft_directory,
                output_dir=output_dir,
                manifest=manifest,
                reservation=reservation,
            )
            pending_state.handle_ref = weakref.ref(handle)
            _pending_registry[token] = pending_state
        timeout_seconds = max(
            0.0,
            (draft.expires_monotonic_ns - time.monotonic_ns()) / 1_000_000_000,
        )
        timer = threading.Timer(timeout_seconds, _expire_pending, args=(token,))
        timer.daemon = True
        pending_state.timer = timer
        timer.start()
        weakref.finalize(handle, _expire_pending, token)
        return handle
    except BaseException as error:
        try:
            supervisor.revoke_external_authority(
                "hardware execution or cleanup failed; physical command state "
                "may be unknown"
            )
        except Exception:
            pass
        for closer in (
            watchdog.stop,
            adapter.close,
            observer.close if observer else None,
        ):
            if closer is not None:
                try:
                    closer()
                except Exception:
                    pass
        if reservation is not None or not output_dir.exists():
            try:
                _publish_unexpected_failure(
                    state,
                    confirmation=confirmation,
                    output_dir=output_dir,
                    error=error,
                    reservation=reservation,
                )
            except Exception:
                pass
        if draft_root is not None:
            shutil.rmtree(draft_root, ignore_errors=True)
        if reservation is not None and reservation.marker.exists():
            _release_reservation(reservation, remove_empty_output=True)
        raise


def _pending_authority(
    handle: PendingPowerRemovalHandle,
) -> tuple[str, _PendingState]:
    token = handle.issuance_token.get_secret_value()
    with _registry_lock:
        state = _pending_registry.get(token)
    if state is None:
        raise ValueError("pending power-removal handle is unknown or consumed")
    if not (
        state.issuing_pid == os.getpid()
        and state.handle_ref is not None
        and state.handle_ref() is handle
        and state.draft == handle.draft
        and _draft_digest(handle.draft) == handle.draft.draft_sha256
    ):
        raise ValueError("pending power-removal authority is invalid")
    return token, state


def _remove_pending(token: str) -> _PendingState | None:
    with _registry_lock:
        state = _pending_registry.pop(token, None)
    if state is not None and state.timer is not None:
        state.timer.cancel()
    return state


def _pending_aborted_files(
    state: _PendingState, *, error_type: str
) -> dict[str, bytes]:
    failure_payload = json.dumps(
        {
            "schema_version": "pending-hardware-abandonment/v1",
            "run_id": state.draft.run_id,
            "error_type": error_type,
            "power_removal_required": True,
            "completion_eligible": False,
            "draft_sha256": state.draft.draft_sha256,
            "output_identity_sha256": state.draft.output_identity_sha256,
        },
        sort_keys=True,
        indent=2,
    ).encode()
    artifacts = dict(state.manifest.artifacts)
    artifacts["pending-abandonment.json"] = _artifact_record(
        "pending-abandonment.json", failure_payload
    )
    aborted = ArtifactManifest.model_validate(
        {
            **state.manifest.model_dump(mode="python"),
            "status": RunStatus.ABORTED,
            "ended_at": datetime.now(UTC),
            "artifacts": artifacts,
            "aborted_reason": (
                "hardware run incomplete before power-removal finalization"
            ),
            "failure": FailureRecord(
                category=FailureCategory.INTERRUPTED,
                error_type=error_type,
            ),
        }
    )
    files = {
        str(path.relative_to(state.draft_directory)): path.read_bytes()
        for path in state.draft_directory.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "draft.json"}
    }
    files["pending-abandonment.json"] = failure_payload
    files["manifest.json"] = json.dumps(
        aborted.model_dump(mode="json"), sort_keys=True, indent=2
    ).encode()
    return files


def _terminalize_pending(token: str, *, error_type: str) -> None:
    with _registry_lock:
        state = _pending_registry.get(token)
    if state is None:
        return
    try:
        _publish_reserved_generation(
            state.reservation,
            _pending_aborted_files(state, error_type=error_type),
        )
    except Exception:
        retry = threading.Timer(
            1.0, _terminalize_pending, kwargs={"token": token, "error_type": error_type}
        )
        retry.daemon = True
        state.timer = retry
        retry.start()
        return
    _remove_pending(token)
    shutil.rmtree(state.draft_directory.parent, ignore_errors=True)


def _expire_pending(token: str) -> None:
    _terminalize_pending(token, error_type="PowerRemovalConfirmationExpired")


def abandon_pending_hardware_identification(
    pending: PendingPowerRemovalHandle,
) -> None:
    token, _ = _pending_authority(pending)
    _terminalize_pending(token, error_type="PendingHardwareRunAbandoned")


def finalize_hardware_identification(
    *,
    pending: PendingPowerRemovalHandle,
    confirmation: PowerRemovalConfirmation,
) -> ArtifactManifest:
    """Publish COMPLETED only after a fresh, bound operator power-OFF fact."""

    token, state = _pending_authority(pending)
    draft = state.draft
    if not (
        confirmation.run_id == draft.run_id
        and confirmation.challenge_id == draft.challenge_id
        and confirmation.config_sha256 == draft.config_sha256
        and confirmation.manifest_sha256 == draft.manifest_sha256
        and confirmation.draft_sha256 == draft.draft_sha256
    ):
        raise ValueError("power-removal confirmation is not bound to the staged draft")
    now_wall, now_ns = datetime.now(UTC), time.monotonic_ns()
    if confirmation.confirmed_at > now_wall or (
        confirmation.confirmed_monotonic_ns > now_ns
    ):
        raise ValueError("power-removal confirmation timestamp is in the future")
    if confirmation.confirmed_monotonic_ns < draft.cleanup_completed_monotonic_ns:
        raise ValueError("power removal must be confirmed after cleanup")
    if confirmation.confirmed_monotonic_ns >= draft.expires_monotonic_ns or (
        now_ns >= draft.expires_monotonic_ns
    ):
        raise ValueError("power-removal confirmation is stale")
    if now_ns - confirmation.confirmed_monotonic_ns >= 60_000_000_000:
        raise ValueError("power-removal confirmation is stale")
    removal = PowerRemovalProvenance.model_validate(
        confirmation.model_dump(mode="python")
    )
    shutdown = HardwareShutdownProvenance(
        final_home_verified=True,
        watchdog_stopped=True,
        adapter_closed=True,
        observer_closed=True,
        motion_ended_monotonic_ns=draft.motion_ended_monotonic_ns,
        cleanup_completed_monotonic_ns=draft.cleanup_completed_monotonic_ns,
        output_identity_sha256=draft.output_identity_sha256,
        power_removal=removal,
    )
    metadata = state.manifest.identification_metadata
    if metadata is None:
        raise RuntimeError("staged hardware manifest lacks identification metadata")
    final_manifest = ArtifactManifest.model_validate(
        {
            **state.manifest.model_dump(mode="python"),
            "status": RunStatus.COMPLETED,
            "ended_at": now_wall,
            "identification_metadata": metadata.model_copy(
                update={"shutdown_provenance": shutdown}
            ),
        }
    )
    files = {
        str(path.relative_to(state.draft_directory)): path.read_bytes()
        for path in state.draft_directory.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "draft.json"}
    }
    files["manifest.json"] = json.dumps(
        final_manifest.model_dump(mode="json"), sort_keys=True, indent=2
    ).encode()
    _publish_reserved_generation(state.reservation, files)
    _remove_pending(token)
    shutil.rmtree(state.draft_directory.parent, ignore_errors=True)
    return final_manifest


def cancel_prepared_hardware_identification(
    prepared: PreparedHardwareHandle,
) -> None:
    state = _consume_handle(prepared)
    _close_state(state, "prepared hardware run cancelled before power enable")
