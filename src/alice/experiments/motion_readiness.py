"""Read-only identity and deterministic replay gate for streaming motion."""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import numpy as np
import torch
import yaml
from pydantic import BaseModel, ConfigDict, JsonValue

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.hardware.manifest import HardwareManifest
from alice.models.package import (
    FilesystemIdentity,
    LoadedMotionModel,
    PackageIdentities,
    PackageSnapshot,
    load_package_snapshot,
    snapshot_package,
)
from alice.motion.intent_filter import FilteredIntent, SupportStatus
from alice.motion.state import ActuatorVelocity, GeneratorState, dump_state
from alice.motion.streaming import StreamingMotionGenerator
from alice.speech.device_identity import (
    CameraDeviceExpectation as CameraDeviceExpectation,
)
from alice.speech.device_identity import (
    ControllerDeviceExpectation as ControllerDeviceExpectation,
)
from alice.speech.device_identity import (
    DeviceNodeSnapshot as DeviceNodeSnapshot,
)
from alice.speech.device_identity import (
    ReadinessDeviceConfig,
    StableDeviceIdentity,
    _read_regular_file,
    _snapshot_device,
    _snapshot_readiness_device_config,
    _stat_identity,
)
from alice.speech.device_identity import (
    inspect_camera_identity as _inspect_camera_identity,
)
from alice.speech.device_identity import (
    inspect_controller_identity as _inspect_controller_identity,
)
from alice.speech.device_identity import (
    load_readiness_device_config as load_readiness_device_config,
)

_REPLAY_DURATION_SECONDS: Literal[60] = 60
_REPLAY_SEEDS = (7, 29)
_AFFECT_VECTORS = (
    (-0.6, 0.2, 0.4),
    (0.0, 0.0, 0.0),
    (0.6, 0.8, -0.3),
)
_PREFIX_DURATION_SECONDS = 0.4
_HORIZON_SECONDS = 1.0


class ReadinessCheck(BaseModel):
    """One compact, automation-readable readiness result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str
    status: Literal["pass", "fail"]
    reason: str
    error_code: str | None = None


class ReadinessIdentities(BaseModel):
    """Exact non-secret identities bound by the readiness gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hardware_id: str
    controller_serial: str
    camera_id: str
    affect_schema_id: str
    motion_model_id: str
    calibration_sha256: str
    controller_response_model_id: str
    controller_settings_sha256: str
    controller_response_sha256: str
    hardware_manifest_sha256: str
    model_package_manifest_sha256: str


class ReplayEvidence(BaseModel):
    """Derived-only summary of deterministic mock/controller replay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    duration_seconds: Literal[60]
    seeds: tuple[int, ...]
    affect_vector_count: int
    prefix_count: int
    digest_sha256: str


class ReadinessReport(BaseModel):
    """Read-only gate result; it contains no frames or actuator commands."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["motion-readiness-report/v1"]
    status: Literal["pass", "fail"]
    read_only: Literal[True]
    checks: tuple[ReadinessCheck, ...]
    identities: ReadinessIdentities | None = None
    replay: ReplayEvidence | None = None


@dataclass(frozen=True, slots=True)
class ReadinessExecution:
    """A report plus input identities required for non-destructive publication."""

    report: ReadinessReport
    protected_file_identities: frozenset[FilesystemIdentity]
    protected_directory_identities: frozenset[FilesystemIdentity]


class OutputPublicationError(RuntimeError):
    """A readiness report destination failed a non-destructive safety check."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def publish_readiness_report(
    path: str | Path,
    report: ReadinessReport,
    *,
    protected_file_identities: frozenset[FilesystemIdentity],
    protected_directory_identities: frozenset[FilesystemIdentity],
) -> None:
    """Publish one new regular report without following links or replacing data."""

    destination = Path(path)
    if not destination.name or destination.name in {".", ".."}:
        raise OutputPublicationError("output-destination-invalid")
    parent_fd = _open_real_directory(destination.parent)
    temp_name: str | None = None
    temp_fd = -1
    try:
        parent_identity = _stat_identity(os.fstat(parent_fd))
        if parent_identity in protected_directory_identities:
            raise OutputPublicationError("output-parent-aliases-input")
        _reject_existing_destination(
            parent_fd,
            destination.name,
            protected_file_identities=protected_file_identities,
        )
        payload = report.model_dump_json(indent=2).encode("utf-8") + b"\n"
        for _ in range(8):
            candidate = f".{destination.name}.{secrets.token_hex(12)}.tmp"
            try:
                temp_fd = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                continue
            temp_name = candidate
            break
        if temp_name is None or temp_fd < 0:
            raise OutputPublicationError("output-temporary-unavailable")
        metadata = os.fstat(temp_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OutputPublicationError("output-temporary-non-regular")
        if _stat_identity(metadata) in protected_file_identities:
            raise OutputPublicationError("output-aliases-input")
        view = memoryview(payload)
        while view:
            written = os.write(temp_fd, view)
            if written <= 0:
                raise OutputPublicationError("output-write-failed")
            view = view[written:]
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = -1
        try:
            os.link(
                temp_name,
                destination.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            _reject_existing_destination(
                parent_fd,
                destination.name,
                protected_file_identities=protected_file_identities,
            )
            raise OutputPublicationError("output-destination-exists") from error
        os.unlink(temp_name, dir_fd=parent_fd)
        temp_name = None
        os.fsync(parent_fd)
    except OutputPublicationError:
        raise
    except OSError as error:
        raise OutputPublicationError("output-publication-failed") from error
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except OSError:
                pass
        os.close(parent_fd)


def _reject_existing_destination(
    parent_fd: int,
    name: str,
    *,
    protected_file_identities: frozenset[FilesystemIdentity],
) -> None:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    identity = _stat_identity(metadata)
    if stat.S_ISLNK(metadata.st_mode):
        raise OutputPublicationError("output-destination-symlink")
    if identity in protected_file_identities:
        raise OutputPublicationError("output-aliases-input")
    if not stat.S_ISREG(metadata.st_mode):
        raise OutputPublicationError("output-destination-non-regular")
    raise OutputPublicationError("output-destination-exists")


def _open_real_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if ".." in path.parts:
        raise OutputPublicationError("output-parent-invalid")
    current_fd = os.open("/" if path.is_absolute() else ".", flags)
    components = path.parts[1:] if path.is_absolute() else path.parts
    try:
        for component in components:
            if component in {"", "."}:
                continue
            metadata = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                raise OutputPublicationError("output-parent-symlink")
            if not stat.S_ISDIR(metadata.st_mode):
                raise OutputPublicationError("output-parent-non-directory")
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def run_readiness(
    *,
    hardware_manifest: str | Path,
    device_config: str | Path,
    model_package: str | Path,
    camera_device: str | Path,
) -> ReadinessReport:
    """Run the gate and return only its non-sensitive, derived report."""

    return execute_readiness(
        hardware_manifest=hardware_manifest,
        device_config=device_config,
        model_package=model_package,
        camera_device=camera_device,
    ).report


def execute_readiness(
    *,
    hardware_manifest: str | Path,
    device_config: str | Path,
    model_package: str | Path,
    camera_device: str | Path,
) -> ReadinessExecution:
    """Snapshot inputs, verify device metadata, and run software-only replay."""

    checks: list[ReadinessCheck] = []
    protected_files: set[FilesystemIdentity] = set()
    protected_directories: set[FilesystemIdentity] = set()
    manifest: HardwareManifest | None = None
    manifest_sha256: str | None = None
    try:
        manifest_bytes, manifest_identity = _read_regular_file(hardware_manifest)
        protected_files.add(manifest_identity)
        document = yaml.safe_load(manifest_bytes)
        if not isinstance(document, dict):
            raise ValueError("hardware manifest root must be a mapping")
        manifest = HardwareManifest.model_validate(document)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        checks.append(_passed("hardware-manifest", "validated immutable snapshot"))
    except Exception:
        checks.append(
            _failed(
                "hardware-manifest",
                "hardware-manifest-invalid",
                "hardware manifest validation failed",
            )
        )

    config: ReadinessDeviceConfig | None = None
    try:
        config, config_identity = _snapshot_readiness_device_config(device_config)
        protected_files.add(config_identity)
        if manifest is not None and (
            config.controller.stable_path != manifest.controller.command_device_path
            or config.controller.usb_serial != manifest.controller.serial_number
        ):
            raise ValueError("device config and hardware manifest disagree")
        checks.append(_passed("device-config", "validated immutable snapshot"))
    except Exception:
        checks.append(
            _failed(
                "device-config",
                "device-config-invalid",
                "readiness device configuration validation failed",
            )
        )

    controller: StableDeviceIdentity | None = None
    camera: StableDeviceIdentity | None = None
    if manifest is not None and config is not None:
        try:
            controller = inspect_controller_identity(
                manifest.controller.command_device_path,
                config.controller,
            )
            protected_files.add(
                FilesystemIdentity(
                    device=controller.node_device,
                    inode=controller.node_inode,
                )
            )
            checks.append(
                _passed(
                    "controller-identity",
                    "canonical by-id, kernel, and USB identities match",
                )
            )
        except Exception:
            checks.append(
                _failed(
                    "controller-identity",
                    "controller-identity-invalid",
                    "controller identity validation failed",
                )
            )
        try:
            camera = inspect_camera_identity(camera_device, config.camera)
            protected_files.add(
                FilesystemIdentity(device=camera.node_device, inode=camera.node_inode)
            )
            checks.append(
                _passed(
                    "camera-identity",
                    "canonical by-id, kernel, and USB identities match",
                )
            )
        except Exception:
            checks.append(
                _failed(
                    "camera-identity",
                    "camera-identity-invalid",
                    "camera identity validation failed",
                )
            )
    else:
        checks.extend(
            (
                _failed(
                    "controller-identity",
                    "identity-prerequisite-failed",
                    "identity prerequisite validation failed",
                ),
                _failed(
                    "camera-identity",
                    "identity-prerequisite-failed",
                    "identity prerequisite validation failed",
                ),
            )
        )

    loaded: LoadedMotionModel | None = None
    identities: PackageIdentities | None = None
    package_manifest_sha256: str | None = None
    package_snapshot: PackageSnapshot | None = None
    try:
        package_snapshot = snapshot_package(model_package)
        protected_files.update(package_snapshot.file_identities)
        protected_directories.update(package_snapshot.directory_identities)
        identities = package_snapshot.identities
        package_manifest_sha256 = package_snapshot.manifest_sha256
        loaded = load_package_snapshot(package_snapshot, identities)
        if manifest is None:
            raise ValueError("hardware manifest is unavailable")
        _validate_exact_identities(manifest, loaded)
        checks.append(
            _passed(
                "package-identities",
                "model, calibration, and controller identities match",
            )
        )
    except Exception:
        checks.append(
            _failed(
                "package-identities",
                "package-identities-invalid",
                "package identity validation failed",
            )
        )

    evidence: ReplayEvidence | None = None
    if all(check.status == "pass" for check in checks):
        if loaded is None or package_manifest_sha256 is None:
            checks.append(
                ReadinessCheck(
                    check_id="motion-replay",
                    status="fail",
                    error_code="replay-prerequisite-failed",
                    reason="replay prerequisite validation failed",
                )
            )
        else:
            try:
                evidence = _run_deterministic_replay(
                    loaded,
                    model_sha256=package_manifest_sha256,
                )
                checks.append(
                    _passed(
                        "motion-replay",
                        "deterministic production/controller replay passed",
                    )
                )
            except Exception:
                checks.append(
                    _failed(
                        "motion-replay",
                        "motion-replay-failed",
                        "deterministic motion replay failed",
                    )
                )
    else:
        checks.append(
            ReadinessCheck(
                check_id="motion-replay",
                status="fail",
                error_code="replay-prerequisite-failed",
                reason="replay prerequisite validation failed",
            )
        )

    bound: ReadinessIdentities | None = None
    if (
        manifest is not None
        and manifest_sha256 is not None
        and controller is not None
        and camera is not None
        and identities is not None
        and package_manifest_sha256 is not None
    ):
        bound = ReadinessIdentities(
            hardware_id=manifest.hardware_id,
            controller_serial=manifest.controller.serial_number,
            camera_id=camera.stable_id,
            affect_schema_id=identities.affect_schema_id,
            motion_model_id=identities.motion_model_id,
            calibration_sha256=identities.calibration_sha256,
            controller_response_model_id=identities.controller_response_model_id,
            controller_settings_sha256=identities.controller_settings_sha256,
            controller_response_sha256=identities.controller_response_sha256,
            hardware_manifest_sha256=manifest_sha256,
            model_package_manifest_sha256=package_manifest_sha256,
        )
    return ReadinessExecution(
        report=_report(checks, identities=bound, replay=evidence),
        protected_file_identities=frozenset(protected_files),
        protected_directory_identities=frozenset(protected_directories),
    )


def _validate_exact_identities(
    manifest: HardwareManifest,
    loaded: LoadedMotionModel,
) -> None:
    identities = loaded.identities
    controller = loaded.controller_response.config
    if identities.calibration_sha256 != manifest.calibration_sha256:
        raise ValueError("calibration identity mismatch between hardware and package")
    if controller.hardware_id != manifest.hardware_id:
        raise ValueError(
            "hardware identity mismatch between manifest and controller config"
        )
    if controller.model_id != identities.controller_response_model_id:
        raise ValueError("controller response model identity mismatch")
    if controller.controller_settings_sha256 != identities.controller_settings_sha256:
        raise ValueError("controller settings identity mismatch")
    if controller.response_sha256 != identities.controller_response_sha256:
        raise ValueError("controller response configuration identity mismatch")
    manifest_names = tuple(actuator.name for actuator in manifest.actuators)
    if manifest_names != loaded.residual_model.config.actuator_names:
        raise ValueError("semantic actuator identity/order mismatch")
    manifest_settings = tuple(
        (actuator.name, actuator.firmware_speed, actuator.firmware_acceleration)
        for actuator in manifest.actuators
    )
    controller_settings = tuple(
        (
            actuator.actuator_name,
            actuator.firmware_speed_setting,
            actuator.firmware_acceleration_setting,
        )
        for actuator in controller.actuators
    )
    if manifest_settings != controller_settings:
        raise ValueError("firmware controller settings mismatch")


def _run_deterministic_replay(
    loaded: LoadedMotionModel,
    *,
    model_sha256: str,
) -> ReplayEvidence:
    first_digest, prefix_count = _replay_digest(loaded, model_sha256=model_sha256)
    second_digest, second_count = _replay_digest(loaded, model_sha256=model_sha256)
    if first_digest != second_digest or prefix_count != second_count:
        raise ValueError("deterministic replay digest mismatch")
    return ReplayEvidence(
        duration_seconds=_REPLAY_DURATION_SECONDS,
        seeds=_REPLAY_SEEDS,
        affect_vector_count=len(_AFFECT_VECTORS),
        prefix_count=prefix_count,
        digest_sha256=first_digest,
    )


def _replay_digest(
    loaded: LoadedMotionModel,
    *,
    model_sha256: str,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    total_prefixes = 0
    prefix_count = round(_REPLAY_DURATION_SECONDS / _PREFIX_DURATION_SECONDS)
    if not math.isclose(
        prefix_count * _PREFIX_DURATION_SECONDS,
        _REPLAY_DURATION_SECONDS,
    ):
        raise RuntimeError("replay duration is not an exact prefix multiple")
    names = loaded.residual_model.config.actuator_names
    initial_target = TargetUpdate(
        offset_s=0.0,
        targets=tuple(
            ActuatorTarget(actuator_name=name, normalized_position=0.0)
            for name in names
        ),
    )
    runtime = StreamingMotionGenerator(
        generator=loaded.candidate_composer,
        horizon_s=_HORIZON_SECONDS,
        prefix_duration_s=_PREFIX_DURATION_SECONDS,
    )
    for seed in _REPLAY_SEEDS:
        initial_intent = _intent(_AFFECT_VECTORS[0], accepted_ns=0, seed=seed)
        numpy_rng = np.random.default_rng(seed)
        torch_rng = torch.Generator(device="cpu").manual_seed(seed)
        state = GeneratorState(
            schema_version="generator-state/v1",
            last_accepted_target=initial_target,
            last_reported_pose=initial_target,
            estimated_velocity=tuple(
                ActuatorVelocity(actuator_name=name, velocity_per_s=0.0)
                for name in names
            ),
            filtered_intent=initial_intent,
            latent_vector=(0.0,) * loaded.residual_model.config.hidden_size,
            numpy_rng_state=cast(
                dict[str, JsonValue],
                dict(numpy_rng.bit_generator.state),
            ),
            torch_rng_state=tuple(int(value) for value in torch_rng.get_state()),
            event_history=(),
            model_id=loaded.identities.motion_model_id,
            model_sha256=model_sha256,
            calibration_sha256=loaded.identities.calibration_sha256,
            controller_settings_sha256=loaded.identities.controller_settings_sha256,
            monotonic_ns=0,
        )
        for index in range(prefix_count):
            now_ns = state.monotonic_ns
            intent = _intent(
                _AFFECT_VECTORS[index % len(_AFFECT_VECTORS)],
                accepted_ns=now_ns,
                seed=seed,
            )
            prefix, state = runtime.replan(intent, state, now_ns)
            digest.update(
                json.dumps(
                    prefix.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            total_prefixes += 1
        digest.update(dump_state(state).encode("utf-8"))
    return digest.hexdigest(), total_prefixes


def _intent(
    vector: tuple[float, float, float],
    *,
    accepted_ns: int,
    seed: int,
) -> FilteredIntent:
    return FilteredIntent(
        schema_version="filtered-intent/v1",
        affect_schema_id="affect-vector/v1",
        vector=vector,
        intensity=0.65,
        source_id=f"readiness-replay-seed-{seed}",
        accepted_monotonic_ns=accepted_ns,
        support_status=SupportStatus.SUPPORTED,
        support_distance=0.0,
        reason="deterministic synthetic readiness replay",
    )


def _passed(check_id: str, reason: str) -> ReadinessCheck:
    return ReadinessCheck(check_id=check_id, status="pass", reason=reason)


def _failed(check_id: str, error_code: str, reason: str) -> ReadinessCheck:
    return ReadinessCheck(
        check_id=check_id,
        status="fail",
        error_code=error_code,
        reason=reason,
    )


def _report(
    checks: list[ReadinessCheck],
    *,
    identities: ReadinessIdentities | None = None,
    replay: ReplayEvidence | None = None,
) -> ReadinessReport:
    status: Literal["pass", "fail"] = (
        "pass" if checks and all(check.status == "pass" for check in checks) else "fail"
    )
    return ReadinessReport(
        schema_version="motion-readiness-report/v1",
        status=status,
        read_only=True,
        checks=tuple(checks),
        identities=identities,
        replay=replay,
    )


def inspect_controller_identity(
    stable_path: str | Path,
    expectation: ControllerDeviceExpectation,
    *,
    sys_tty_root: Path = Path("/sys/class/tty"),
) -> StableDeviceIdentity:
    return _inspect_controller_identity(
        stable_path,
        expectation,
        sys_tty_root=sys_tty_root,
        snapshot_device=_snapshot_device,
    )


def inspect_camera_identity(
    stable_path: str | Path,
    expectation: CameraDeviceExpectation,
    *,
    sys_video_root: Path = Path("/sys/class/video4linux"),
) -> StableDeviceIdentity:
    return _inspect_camera_identity(
        stable_path,
        expectation,
        sys_video_root=sys_video_root,
        snapshot_device=_snapshot_device,
    )
