"""Read-only identity and deterministic replay gate for streaming motion."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Literal, cast

import numpy as np
import torch
import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, JsonValue

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.hardware.manifest import HardwareManifest
from alice.models.package import (
    LoadedMotionModel,
    PackageIdentities,
    load_package,
)
from alice.motion.intent_filter import FilteredIntent, SupportStatus
from alice.motion.state import ActuatorVelocity, GeneratorState, dump_state
from alice.motion.streaming import StreamingMotionGenerator

_CONTROLLER_NAME = re.compile(
    r"^usb-Pololu_Corporation_Pololu_Mini_Maestro_12-Channel_USB_"
    r"Servo_Controller_(?P<serial>[A-Za-z0-9]+)-if(?P<interface>[0-9]{2})$"
)
_CAMERA_NAME = re.compile(r"^usb-[^/\s]+-video-index(?P<index>[0-9]+)$")
_TTY_NAME = re.compile(r"^ttyACM[0-9]+$")
_VIDEO_NAME = re.compile(r"^video[0-9]+$")
_REPLAY_DURATION_SECONDS: Literal[60] = 60
_REPLAY_SEEDS = (7, 29)
_AFFECT_VECTORS = (
    (-0.6, 0.2, 0.4),
    (0.0, 0.0, 0.0),
    (0.6, 0.8, -0.3),
)
_PREFIX_DURATION_SECONDS = 0.4
_HORIZON_SECONDS = 1.0


class StableDeviceIdentity(BaseModel):
    """Non-opening identity evidence for one stable Linux device selector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["controller", "camera"]
    stable_path: str
    stable_id: str
    resolved_device_name: str


class ReadinessCheck(BaseModel):
    """One compact, automation-readable readiness result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str
    status: Literal["pass", "fail"]
    reason: str


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


def inspect_controller_identity(
    stable_path: str | Path,
    expected_serial: str,
    *,
    sys_tty_root: Path = Path("/sys/class/tty"),
) -> StableDeviceIdentity:
    """Verify a Maestro command-port by-id link and its read-only sysfs identity."""

    stable = Path(stable_path)
    if (
        stable.parent.name != "by-id"
        or stable.parent.parent.name != "serial"
        or not stable.is_symlink()
    ):
        raise ValueError("stable controller device must be a serial/by-id symlink")
    match = _CONTROLLER_NAME.fullmatch(stable.name)
    if match is None or match.group("interface") != "00":
        raise ValueError(
            "stable controller device is not a Pololu command-port identity"
        )
    resolved = stable.resolve(strict=True)
    if _TTY_NAME.fullmatch(resolved.name) is None:
        raise ValueError("stable controller device does not resolve to a ttyACM node")

    device = (sys_tty_root / resolved.name / "device").resolve(strict=True)
    serial: str | None = None
    interface: str | None = None
    for ancestor in (device, *device.parents):
        serial_file = ancestor / "serial"
        interface_file = ancestor / "bInterfaceNumber"
        if serial is None and serial_file.is_file():
            serial = serial_file.read_text(encoding="ascii").strip()
        if interface is None and interface_file.is_file():
            interface = interface_file.read_text(encoding="ascii").strip().zfill(2)
        if serial is not None and interface is not None:
            break
    if serial is None or interface is None:
        raise ValueError(
            "controller USB serial/interface identity is unavailable in sysfs"
        )
    if serial != expected_serial or match.group("serial") != expected_serial:
        raise ValueError("controller serial identity mismatch")
    if interface != "00":
        raise ValueError("controller sysfs interface is not command interface 00")
    return StableDeviceIdentity(
        kind="controller",
        stable_path=str(stable),
        stable_id=f"pololu-maestro:{serial}:if{interface}",
        resolved_device_name=resolved.name,
    )


def inspect_camera_identity(
    stable_path: str | Path,
    *,
    sys_video_root: Path = Path("/sys/class/video4linux"),
) -> StableDeviceIdentity:
    """Verify a stable V4L2 capture link and sysfs membership without opening it."""

    stable = Path(stable_path)
    if (
        stable.parent.name != "by-id"
        or stable.parent.parent.name != "v4l"
        or not stable.is_symlink()
    ):
        raise ValueError("camera device must be a stable V4L2 by-id symlink")
    match = _CAMERA_NAME.fullmatch(stable.name)
    if match is None or match.group("index") != "0":
        raise ValueError("camera device must identify V4L2 capture video-index0")
    resolved = stable.resolve(strict=True)
    if _VIDEO_NAME.fullmatch(resolved.name) is None:
        raise ValueError("stable camera device does not resolve to a video node")
    (sys_video_root / resolved.name / "device").resolve(strict=True)
    return StableDeviceIdentity(
        kind="camera",
        stable_path=str(stable),
        stable_id=stable.name,
        resolved_device_name=resolved.name,
    )


def run_readiness(
    *,
    hardware_manifest: str | Path,
    model_package: str | Path,
    camera_device: str | Path,
) -> ReadinessReport:
    """Run the fail-closed gate using only files, symlinks, sysfs, and simulation."""

    checks: list[ReadinessCheck] = []
    try:
        manifest_bytes = Path(hardware_manifest).read_bytes()
        document = yaml.safe_load(manifest_bytes)
        if not isinstance(document, dict):
            raise ValueError("hardware manifest root must be a mapping")
        manifest = HardwareManifest.model_validate(document)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        checks.append(_passed("hardware-manifest", "validated immutable snapshot"))
    except Exception as error:
        checks.append(_failed("hardware-manifest", error))
        return _report(checks)

    controller: StableDeviceIdentity | None = None
    camera: StableDeviceIdentity | None = None
    try:
        controller = inspect_controller_identity(
            manifest.controller.command_device_path,
            manifest.controller.serial_number,
        )
        checks.append(
            _passed("controller-identity", "stable by-id and sysfs identity match")
        )
    except Exception as error:
        checks.append(_failed("controller-identity", error))
    try:
        camera = inspect_camera_identity(camera_device)
        checks.append(
            _passed("camera-identity", "stable V4L2 by-id identity is present")
        )
    except Exception as error:
        checks.append(_failed("camera-identity", error))

    loaded: LoadedMotionModel | None = None
    identities: PackageIdentities | None = None
    package_manifest_sha256: str | None = None
    try:
        package_manifest = Path(model_package) / "manifest.json"
        before = package_manifest.read_bytes()
        package_document = json.loads(before)
        if not isinstance(package_document, dict):
            raise ValueError("model package manifest root must be an object")
        identities = PackageIdentities.model_validate(
            package_document.get("identities")
        )
        package_manifest_sha256 = hashlib.sha256(before).hexdigest()
        loaded = load_package(model_package, identities)
        after = package_manifest.read_bytes()
        if before != after:
            raise ValueError("model package manifest changed during snapshot loading")
        _validate_exact_identities(manifest, loaded)
        checks.append(
            _passed(
                "package-identities",
                "model, calibration, and controller identities match",
            )
        )
    except Exception as error:
        checks.append(_failed("package-identities", error))

    evidence: ReplayEvidence | None = None
    if all(check.status == "pass" for check in checks):
        if loaded is None or package_manifest_sha256 is None:
            checks.append(
                ReadinessCheck(
                    check_id="motion-replay",
                    status="fail",
                    reason="validated package snapshot is unavailable",
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
            except Exception as error:
                checks.append(_failed("motion-replay", error))
    else:
        checks.append(
            ReadinessCheck(
                check_id="motion-replay",
                status="fail",
                reason="not run because an identity prerequisite failed",
            )
        )

    bound: ReadinessIdentities | None = None
    if controller is not None and camera is not None and identities is not None:
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
            model_package_manifest_sha256=package_manifest_sha256 or "0" * 64,
        )
    return _report(checks, identities=bound, replay=evidence)


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


def _failed(check_id: str, error: Exception) -> ReadinessCheck:
    return ReadinessCheck(
        check_id=check_id,
        status="fail",
        reason=f"{type(error).__name__}: {error}",
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
