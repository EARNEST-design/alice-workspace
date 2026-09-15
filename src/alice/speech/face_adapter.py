"""Trusted selected-face adapter factory shared by CLI and ROS."""

from __future__ import annotations

import json
import secrets
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any

from alice.hardware.face_scope import FACE_CHANNELS, face_manifest
from alice.hardware.maestro_face import MaestroFaceAdapter
from alice.hardware.manifest import HardwareManifest
from alice.safety.supervisor import (
    OperatorApproval,
    PreflightEvidence,
    SafetySupervisor,
)
from alice.speech.device_identity import (
    _resolve_linux_usb_identity,
    inspect_controller_identity,
    load_readiness_device_config,
)
from alice.speech.face_runtime import SimulatedFaceDriver
from alice.speech.face_stream import FaceCommandStream
from alice.speech.jaw_trial import trial_limits


def _write(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def _exclusive_transport(path: str, timeout_seconds: float) -> Any:
    import serial

    return serial.Serial(
        path,
        baudrate=9600,
        timeout=timeout_seconds,
        write_timeout=timeout_seconds,
        exclusive=True,
    )


def _check_owners(full: HardwareManifest, *, resolver=None) -> dict[str, object]:
    paths = (
        full.controller.command_device_path,
        full.controller.command_device_path.replace("-if00", "-if02"),
    )
    for path, interface in zip(paths, ("00", "02"), strict=True):
        Path(path).resolve(strict=True)
        identity = (resolver or _resolve_linux_usb_identity)(path)
        if (
            identity.serial_number != "00037376"
            or identity.interface_number != interface
        ):
            raise ValueError("Maestro interface identity mismatch")
    result = subprocess.run(
        ["fuser", *paths], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 1 or result.stderr.strip() or result.stdout.strip():
        raise ValueError(
            "Maestro interfaces are busy or ownership could not be checked"
        )
    return {
        "paths": paths,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def face_factory(
    full: HardwareManifest,
    generation: str,
    hardware: bool,
    report: dict[str, Any],
    output: Path,
    *,
    config_root: Path,
) -> Callable[[Event], FaceCommandStream]:
    def create(cancel: Event) -> FaceCommandStream:
        scope = face_manifest(full)
        gate = SafetySupervisor(
            manifest=scope, clock=time.monotonic_ns, limits=trial_limits()
        )
        token = secrets.token_urlsafe(32)
        raw: MaestroFaceAdapter | None = None
        approval_time = time.monotonic_ns()
        driver: MaestroFaceAdapter | SimulatedFaceDriver
        try:
            if hardware:
                devices = load_readiness_device_config(
                    config_root / "experiments/streaming-motion-readiness-v1.yaml"
                )
                identity = inspect_controller_identity(
                    full.controller.command_device_path, devices.controller
                )
                report["controller_identity"] = identity.model_dump(mode="json")
                report["interface_ownership"] = _check_owners(full)
                raw = MaestroFaceAdapter(
                    manifest=scope,
                    stable_device_path=full.controller.command_device_path,
                    expected_controller_serial="00037376",
                    required_enable_token=token,
                    clock=time.monotonic_ns,
                    permit_verifier=gate.actuation_permit_verifier,
                    transport_factory=_exclusive_transport,
                    timeout_seconds=0.05,
                    settle_timeout_ns=500_000_000,
                    poll_interval_ns=5_000_000,
                )
                raw.open(token, cancel=cancel)
                before = raw.read_only_preflight(tuple(FACE_CHANNELS))
                report["preflight"] = before.model_dump(mode="json")
                _write(output / "startup.json", report)
                if before.controller_error_register:
                    raise RuntimeError("Maestro error register is not clear")
                snapshot = raw.initialize_disabled_home(token)
                report["initialized_preflight"] = snapshot.model_dump(mode="json")
                report["disabled_pwm_start_is_not_measured_mechanics"] = True
                driver = raw
            else:
                driver = SimulatedFaceDriver(scope)
                snapshot = driver.read_only_preflight(tuple(FACE_CHANNELS))
            run_id = f"face-{time.time_ns()}"
            evidence = PreflightEvidence(
                run_id=run_id,
                hardware_id=scope.hardware_id,
                calibration_sha256=scope.calibration_sha256,
                controller_serial=scope.controller.serial_number,
                requirement_results={
                    r.requirement_id: True for r in scope.preflight_requirements
                },
                competing_process_detected=False,
                controller_error_codes=(),
                home_verified=True,
                observed_monotonic_ns=snapshot.observed_monotonic_ns,
            )
            if (
                not gate.preflight(evidence).accepted
                or not gate.arm(
                    OperatorApproval(
                        approval_id="explicit-attended-run"
                        if hardware
                        else "simulated",
                        run_id=run_id,
                        confirmed_monotonic_ns=approval_time,
                    )
                ).accepted
            ):
                raise RuntimeError("selected-face preflight/arming rejected")
            if raw is not None:
                raw.enable_fast_jaw_response(token)
                report["jaw_response_override"] = raw.jaw_response_override
            _write(output / "startup.json", report)
            return FaceCommandStream(
                gate, driver, token, full, generation_id=generation
            )
        except BaseException:
            if raw is not None:
                raw.close()
            raise

    return create
