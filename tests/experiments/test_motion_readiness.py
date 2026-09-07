"""Read-only readiness coverage for streaming affect motion."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest
import serial
import yaml  # type: ignore[import-untyped]

import alice.experiments.motion_readiness as readiness
import alice.experiments.motion_readiness_cli as readiness_cli
import alice.hardware.maestro_adapter as maestro_adapter
import alice.hardware.maestro_protocol as maestro_protocol
from alice.experiments.motion_readiness import (
    ReadinessCheck,
    ReadinessReport,
    StableDeviceIdentity,
)
from alice.perception.camera import OpenCVCamera
from models.test_package import _write_valid_package

ROOT = Path(__file__).parents[2]
HARDWARE_MANIFEST = ROOT / "hardware" / "alice-face-v1.yaml"
CONTROLLER_PATH = (
    "/dev/serial/by-id/"
    "usb-Pololu_Corporation_Pololu_Mini_Maestro_12-Channel_USB_"
    "Servo_Controller_00037376-if00"
)
CAMERA_PATH = "/dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index0"


def _forbid_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("readiness attempted forbidden hardware access")

    monkeypatch.setattr(serial, "Serial", fail)
    monkeypatch.setattr(serial.SerialBase, "write", fail)
    monkeypatch.setattr(maestro_adapter, "MaestroAdapter", fail)
    monkeypatch.setattr(maestro_protocol, "encode_set_target", fail)
    monkeypatch.setattr(cv2, "VideoCapture", fail)
    monkeypatch.setattr(OpenCVCamera, "open", fail)
    monkeypatch.setattr(OpenCVCamera, "read", fail)


def _device_identity(
    *, kind: str, stable_path: str, stable_id: str
) -> StableDeviceIdentity:
    return StableDeviceIdentity(
        kind=kind,
        stable_path=stable_path,
        stable_id=stable_id,
        resolved_device_name=("ttyACM0" if kind == "controller" else "video0"),
    )


def _install_device_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        readiness,
        "inspect_controller_identity",
        lambda path, expected_serial: _device_identity(
            kind="controller",
            stable_path=str(path),
            stable_id=f"pololu-maestro:{expected_serial}:if00",
        ),
    )
    monkeypatch.setattr(
        readiness,
        "inspect_camera_identity",
        lambda path: _device_identity(
            kind="camera",
            stable_path=str(path),
            stable_id=Path(path).name,
        ),
    )


def _make_controller_tree(tmp_path: Path) -> tuple[Path, Path]:
    device_root = tmp_path / "dev"
    tty = device_root / "ttyACM0"
    tty.parent.mkdir(parents=True)
    tty.touch()
    stable = (
        device_root
        / "serial"
        / "by-id"
        / (
            "usb-Pololu_Corporation_Pololu_Mini_Maestro_12-Channel_USB_"
            "Servo_Controller_00037376-if00"
        )
    )
    stable.parent.mkdir(parents=True)
    stable.symlink_to(Path("../..") / tty.name)

    interface = tmp_path / "sys-devices" / "1-3" / "1-3:1.0"
    interface.mkdir(parents=True)
    (interface / "bInterfaceNumber").write_text("00\n", encoding="ascii")
    (interface.parent / "serial").write_text("00037376\n", encoding="ascii")
    sys_tty = tmp_path / "sys" / "class" / "tty" / tty.name
    sys_tty.mkdir(parents=True)
    (sys_tty / "device").symlink_to(interface, target_is_directory=True)
    return stable, tmp_path / "sys" / "class" / "tty"


def _make_camera_tree(tmp_path: Path) -> tuple[Path, Path]:
    device_root = tmp_path / "dev"
    video = device_root / "video0"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.touch()
    stable = (
        device_root
        / "v4l"
        / "by-id"
        / ("usb-046d_HD_Webcam_C525_79C73260-video-index0")
    )
    stable.parent.mkdir(parents=True)
    stable.symlink_to(Path("../..") / video.name)

    interface = tmp_path / "camera-device" / "1-4:1.0"
    interface.mkdir(parents=True)
    sys_video = tmp_path / "sys" / "class" / "video4linux" / video.name
    sys_video.mkdir(parents=True)
    (sys_video / "device").symlink_to(interface, target_is_directory=True)
    return stable, tmp_path / "sys" / "class" / "video4linux"


def test_stable_device_resolution_uses_by_id_symlinks_and_sysfs(tmp_path: Path) -> None:
    """Accepting basename text without matching sysfs ancestry would be unsafe."""

    controller, sys_tty = _make_controller_tree(tmp_path)
    camera, sys_video = _make_camera_tree(tmp_path)

    controller_identity = readiness.inspect_controller_identity(
        controller,
        expected_serial="00037376",
        sys_tty_root=sys_tty,
    )
    camera_identity = readiness.inspect_camera_identity(
        camera,
        sys_video_root=sys_video,
    )

    assert controller_identity.stable_id == "pololu-maestro:00037376:if00"
    assert controller_identity.resolved_device_name == "ttyACM0"
    assert camera_identity.stable_id == camera.name
    assert camera_identity.resolved_device_name == "video0"


@pytest.mark.parametrize("failure", ["missing", "wrong-serial", "direct-camera"])
def test_stale_or_wrong_device_identities_fail_closed(
    tmp_path: Path,
    failure: str,
) -> None:
    """Missing links, serial drift, or unstable camera selectors must be rejected."""

    if failure == "direct-camera":
        direct = tmp_path / "dev" / "video0"
        direct.parent.mkdir(parents=True)
        direct.touch()
        with pytest.raises(ValueError, match="stable V4L2 by-id"):
            readiness.inspect_camera_identity(direct)
        return

    controller, sys_tty = _make_controller_tree(tmp_path)
    if failure == "missing":
        controller.unlink()
        with pytest.raises(ValueError, match="symlink"):
            readiness.inspect_controller_identity(
                controller,
                expected_serial="00037376",
                sys_tty_root=sys_tty,
            )
        return

    with pytest.raises(ValueError, match="serial identity mismatch"):
        readiness.inspect_controller_identity(
            controller,
            expected_serial="99999999",
            sys_tty_root=sys_tty,
        )


def test_readiness_validates_exact_identities_and_deterministic_60_second_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity drift, nondeterminism, or a shortened replay must block readiness."""

    package = _write_valid_package(tmp_path)
    _forbid_hardware(monkeypatch)
    _install_device_probes(monkeypatch)

    first = readiness.run_readiness(
        hardware_manifest=HARDWARE_MANIFEST,
        model_package=package.path,
        camera_device=CAMERA_PATH,
    )
    second = readiness.run_readiness(
        hardware_manifest=HARDWARE_MANIFEST,
        model_package=package.path,
        camera_device=CAMERA_PATH,
    )

    assert first.status == "pass"
    assert all(check.status == "pass" for check in first.checks)
    assert first.identities is not None
    assert first.identities.hardware_id == "alice-face-v1"
    assert first.identities.controller_serial == "00037376"
    assert first.identities.motion_model_id == "streaming-affect-motion-test-v1"
    assert first.identities.calibration_sha256 == (
        "8ad9ad1f59dc70c47515c9a37b4531c5070c22a40ed66fb673d0e72a0d3dadb4"
    )
    assert first.identities.controller_response_model_id == "maestro-response-v1"
    assert first.replay is not None
    assert first.replay.duration_seconds == 60
    assert first.replay.seeds == (7, 29)
    assert first.replay.affect_vector_count == 3
    assert first.replay.digest_sha256 == second.replay.digest_sha256  # type: ignore[union-attr]


def test_identity_mismatch_and_replay_exception_are_structured_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mismatch or replay crash must produce a structured failed check."""

    package = _write_valid_package(tmp_path)
    _forbid_hardware(monkeypatch)
    _install_device_probes(monkeypatch)
    changed = yaml.safe_load(HARDWARE_MANIFEST.read_text(encoding="utf-8"))
    changed["hardware_id"] = "wrong-hardware"
    mismatched_manifest = tmp_path / "wrong-hardware.yaml"
    mismatched_manifest.write_text(yaml.safe_dump(changed), encoding="utf-8")

    mismatched = readiness.run_readiness(
        hardware_manifest=mismatched_manifest,
        model_package=package.path,
        camera_device=CAMERA_PATH,
    )

    assert mismatched.status == "fail"
    assert mismatched.replay is None
    mismatch_check = next(
        check for check in mismatched.checks if check.check_id == "package-identities"
    )
    assert mismatch_check.status == "fail"
    assert "calibration identity mismatch" in mismatch_check.reason

    monkeypatch.setattr(
        readiness,
        "_run_deterministic_replay",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("synthetic replay fault")
        ),
    )
    replay_failed = readiness.run_readiness(
        hardware_manifest=HARDWARE_MANIFEST,
        model_package=package.path,
        camera_device=CAMERA_PATH,
    )

    assert replay_failed.status == "fail"
    replay_check = next(
        check for check in replay_failed.checks if check.check_id == "motion-replay"
    )
    assert replay_check.status == "fail"
    assert replay_check.reason == "RuntimeError: synthetic replay fault"


def test_cli_returns_nonzero_and_writes_compact_json_on_failed_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Automation needs a nonzero result plus machine-readable failure evidence."""

    failed = ReadinessReport(
        schema_version="motion-readiness-report/v1",
        status="fail",
        read_only=True,
        checks=(
            ReadinessCheck(
                check_id="controller-identity",
                status="fail",
                reason="stable controller device is unavailable",
            ),
        ),
    )
    monkeypatch.setattr(readiness_cli, "run_readiness", lambda **kwargs: failed)
    output = tmp_path / "readiness.json"

    result = readiness_cli.main(
        [
            "--hardware-manifest",
            str(HARDWARE_MANIFEST),
            "--model-package",
            str(tmp_path / "package"),
            "--camera-device",
            CAMERA_PATH,
            "--json-output",
            str(output),
        ]
    )

    assert result == 2
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["status"] == "fail"
    assert document["read_only"] is True
    assert document["checks"][0]["reason"] == "stable controller device is unavailable"
