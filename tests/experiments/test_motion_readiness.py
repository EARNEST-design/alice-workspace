"""Read-only readiness coverage for streaming affect motion."""

from __future__ import annotations

import json
import os
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
from alice.models.package import FilesystemIdentity
from alice.perception.camera import OpenCVCamera
from models.test_package import _write_valid_package

ROOT = Path(__file__).parents[2]
HARDWARE_MANIFEST = ROOT / "hardware" / "alice-face-v1.yaml"
DEVICE_CONFIG = ROOT / "config" / "experiments" / "streaming-motion-readiness-v1.yaml"
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
        node_device=17,
        node_inode=23 if kind == "controller" else 29,
        rdev_major=166 if kind == "controller" else 81,
        rdev_minor=0,
    )


def _install_device_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        readiness,
        "inspect_controller_identity",
        lambda path, expectation: _device_identity(
            kind="controller",
            stable_path=str(path),
            stable_id=f"pololu-maestro:{expectation.usb_serial}:if00",
        ),
    )
    monkeypatch.setattr(
        readiness,
        "inspect_camera_identity",
        lambda path, expectation: _device_identity(
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


def test_lookalike_device_trees_are_not_canonical_device_identities(
    tmp_path: Path,
) -> None:
    """A user-writable by-id/sysfs lookalike must never count as connected hardware."""

    controller, sys_tty = _make_controller_tree(tmp_path)
    camera, sys_video = _make_camera_tree(tmp_path)
    config = readiness.load_readiness_device_config(DEVICE_CONFIG)

    with pytest.raises(ValueError, match="canonical"):
        readiness.inspect_controller_identity(
            controller,
            config.controller,
            sys_tty_root=sys_tty,
        )
    with pytest.raises(ValueError, match="canonical"):
        readiness.inspect_camera_identity(
            camera,
            config.camera,
            sys_video_root=sys_video,
        )


def test_device_config_pins_exact_controller_and_c525_usb_identities() -> None:
    """A CLI camera argument cannot be its own identity authority."""

    config = readiness.load_readiness_device_config(DEVICE_CONFIG)

    assert config.controller.stable_path == CONTROLLER_PATH
    assert config.controller.usb_vendor_id == "1ffb"
    assert config.controller.usb_product_id == "008a"
    assert config.controller.usb_serial == "00037376"
    assert config.controller.usb_interface == "00"
    assert config.camera.stable_path == CAMERA_PATH
    assert config.camera.usb_vendor_id == "046d"
    assert config.camera.usb_product_id == "0826"
    assert config.camera.usb_serial == "79C73260"
    assert config.camera.usb_interface == "02"
    assert config.camera.video_index == 0


@pytest.mark.parametrize(
    ("field", "unsafe_value", "error"),
    [
        ("node_kind", "regular", "character device"),
        ("kernel_major", 82, "kernel device number"),
        ("usb_vendor_id", "ffff", "USB identity"),
        ("usb_product_id", "ffff", "USB identity"),
        ("usb_serial", "substituted", "USB identity"),
        ("usb_interface", "01", "USB identity"),
        ("device_index", 1, "device index"),
    ],
)
def test_camera_rejects_regular_nodes_and_kernel_or_usb_substitution(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    unsafe_value: object,
    error: str,
) -> None:
    """Filename mimicry cannot substitute a different kernel or USB camera."""

    config = readiness.load_readiness_device_config(DEVICE_CONFIG)
    values = {
        "kind": "camera",
        "stable_path": CAMERA_PATH,
        "node_name": "video2",
        "node_kind": "character",
        "node_identity": FilesystemIdentity(device=17, inode=23),
        "rdev_major": 81,
        "rdev_minor": 2,
        "kernel_major": 81,
        "kernel_minor": 2,
        "usb_vendor_id": "046d",
        "usb_product_id": "0826",
        "usb_serial": "79C73260",
        "usb_interface": "02",
        "device_index": 0,
    }
    values[field] = unsafe_value
    snapshot = readiness.DeviceNodeSnapshot(**values)
    monkeypatch.setattr(readiness, "_snapshot_device", lambda *args: snapshot)

    with pytest.raises(ValueError, match=error):
        readiness.inspect_camera_identity(CAMERA_PATH, config.camera)


@pytest.mark.parametrize(
    ("field", "unsafe_value", "error"),
    [
        ("node_kind", "regular", "character device"),
        ("kernel_minor", 99, "kernel device number"),
        ("usb_vendor_id", "ffff", "USB identity"),
        ("usb_product_id", "ffff", "USB identity"),
        ("usb_serial", "substituted", "USB identity"),
        ("usb_interface", "02", "USB identity"),
    ],
)
def test_controller_rejects_regular_nodes_and_kernel_or_usb_substitution(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    unsafe_value: object,
    error: str,
) -> None:
    """A matching Maestro filename is insufficient without kernel/USB identity."""

    config = readiness.load_readiness_device_config(DEVICE_CONFIG)
    values = {
        "kind": "controller",
        "stable_path": CONTROLLER_PATH,
        "node_name": "ttyACM0",
        "node_kind": "character",
        "node_identity": FilesystemIdentity(device=17, inode=23),
        "rdev_major": 166,
        "rdev_minor": 0,
        "kernel_major": 166,
        "kernel_minor": 0,
        "usb_vendor_id": "1ffb",
        "usb_product_id": "008a",
        "usb_serial": "00037376",
        "usb_interface": "00",
        "device_index": None,
    }
    values[field] = unsafe_value
    snapshot = readiness.DeviceNodeSnapshot(**values)
    monkeypatch.setattr(readiness, "_snapshot_device", lambda *args: snapshot)

    with pytest.raises(ValueError, match=error):
        readiness.inspect_controller_identity(CONTROLLER_PATH, config.controller)


@pytest.mark.parametrize("destination_kind", ["existing", "symlink", "device"])
def test_report_publication_never_overwrites_or_opens_existing_destinations(
    tmp_path: Path,
    destination_kind: str,
) -> None:
    """Opening an existing output path could overwrite input data or a device."""

    report = ReadinessReport(
        schema_version="motion-readiness-report/v1",
        status="fail",
        read_only=True,
        checks=(
            ReadinessCheck(
                check_id="hardware-manifest",
                status="fail",
                reason="hardware manifest validation failed",
            ),
        ),
    )
    sentinel = tmp_path / "sentinel.yaml"
    sentinel.write_text("do-not-change\n", encoding="utf-8")
    if destination_kind == "existing":
        destination = tmp_path / "report.json"
        destination.write_text("existing\n", encoding="utf-8")
    elif destination_kind == "symlink":
        destination = tmp_path / "report.json"
        destination.symlink_to(sentinel)
    else:
        destination = Path("/dev/null")

    with pytest.raises(readiness.OutputPublicationError) as caught:
        readiness.publish_readiness_report(
            destination,
            report,
            protected_file_identities=frozenset(),
            protected_directory_identities=frozenset(),
        )

    assert caught.value.code in {
        "output-destination-exists",
        "output-destination-symlink",
        "output-destination-non-regular",
    }
    assert sentinel.read_text(encoding="utf-8") == "do-not-change\n"


def test_report_publication_rejects_input_alias_and_package_directory(
    tmp_path: Path,
) -> None:
    """Output must not alias an input inode or add files to the package snapshot."""

    package = _write_valid_package(tmp_path)
    snapshot = readiness.snapshot_package(package.path)
    report = ReadinessReport(
        schema_version="motion-readiness-report/v1",
        status="pass",
        read_only=True,
        checks=(
            ReadinessCheck(check_id="motion-replay", status="pass", reason="passed"),
        ),
    )
    alias = tmp_path / "manifest-alias.json"
    alias.hardlink_to(package.manifest)

    with pytest.raises(readiness.OutputPublicationError) as alias_error:
        readiness.publish_readiness_report(
            alias,
            report,
            protected_file_identities=snapshot.file_identities,
            protected_directory_identities=snapshot.directory_identities,
        )
    with pytest.raises(readiness.OutputPublicationError) as directory_error:
        readiness.publish_readiness_report(
            package.path / "readiness.json",
            report,
            protected_file_identities=snapshot.file_identities,
            protected_directory_identities=snapshot.directory_identities,
        )

    assert alias_error.value.code == "output-aliases-input"
    assert directory_error.value.code == "output-parent-aliases-input"
    assert not (package.path / "readiness.json").exists()


def test_report_publication_is_exclusive_and_rejects_symlinked_parent(
    tmp_path: Path,
) -> None:
    """A swapped parent or destination race must not redirect publication."""

    report = ReadinessReport(
        schema_version="motion-readiness-report/v1",
        status="pass",
        read_only=True,
        checks=(
            ReadinessCheck(check_id="motion-replay", status="pass", reason="passed"),
        ),
    )
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(readiness.OutputPublicationError) as caught:
        readiness.publish_readiness_report(
            linked_parent / "report.json",
            report,
            protected_file_identities=frozenset(),
            protected_directory_identities=frozenset(),
        )

    assert caught.value.code == "output-parent-symlink"
    assert not (real_parent / "report.json").exists()


def test_noncanonical_device_arguments_fail_closed(tmp_path: Path) -> None:
    """Direct nodes and alternate identities cannot override reviewed config."""

    config = readiness.load_readiness_device_config(DEVICE_CONFIG)
    direct = tmp_path / "dev" / "video0"
    direct.parent.mkdir(parents=True)
    direct.touch()
    with pytest.raises(ValueError, match="canonical"):
        readiness.inspect_camera_identity(direct, config.camera)
    with pytest.raises(ValueError, match="canonical"):
        readiness.inspect_controller_identity(
            CONTROLLER_PATH.replace("00037376", "99999999"),
            config.controller,
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
        device_config=DEVICE_CONFIG,
        model_package=package.path,
        camera_device=CAMERA_PATH,
    )
    second = readiness.run_readiness(
        hardware_manifest=HARDWARE_MANIFEST,
        device_config=DEVICE_CONFIG,
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
        device_config=DEVICE_CONFIG,
        model_package=package.path,
        camera_device=CAMERA_PATH,
    )

    assert mismatched.status == "fail"
    assert mismatched.replay is None
    mismatch_check = next(
        check for check in mismatched.checks if check.check_id == "package-identities"
    )
    assert mismatch_check.status == "fail"
    assert mismatch_check.error_code == "package-identities-invalid"
    assert mismatch_check.reason == "package identity validation failed"

    monkeypatch.setattr(
        readiness,
        "_run_deterministic_replay",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("SECRET synthetic replay fault /tmp/private-input")
        ),
    )
    replay_failed = readiness.run_readiness(
        hardware_manifest=HARDWARE_MANIFEST,
        device_config=DEVICE_CONFIG,
        model_package=package.path,
        camera_device=CAMERA_PATH,
    )

    assert replay_failed.status == "fail"
    replay_check = next(
        check for check in replay_failed.checks if check.check_id == "motion-replay"
    )
    assert replay_check.status == "fail"
    assert replay_check.error_code == "motion-replay-failed"
    assert replay_check.reason == "deterministic motion replay failed"
    assert "SECRET" not in replay_failed.model_dump_json()
    assert "/tmp/private-input" not in replay_failed.model_dump_json()


@pytest.mark.parametrize("input_name", ["hardware-manifest", "device-config"])
def test_special_file_inputs_fail_without_being_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_name: str,
) -> None:
    """Input pathnames may never turn FIFOs or device nodes into byte streams."""

    package = _write_valid_package(tmp_path)
    _forbid_hardware(monkeypatch)
    _install_device_probes(monkeypatch)
    kwargs = {
        "hardware_manifest": HARDWARE_MANIFEST,
        "device_config": DEVICE_CONFIG,
        "model_package": package.path,
        "camera_device": CAMERA_PATH,
    }
    kwargs[input_name.replace("-", "_")] = Path("/dev/null")

    report = readiness.run_readiness(**kwargs)

    assert report.status == "fail"
    check = next(item for item in report.checks if item.check_id == input_name)
    assert check.error_code == f"{input_name}-invalid"
    assert "/dev/null" not in report.model_dump_json()


def test_special_input_is_inspected_without_a_read_capable_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device pathname must fail before readiness can acquire read access."""

    original_open = os.open
    inspected_flags: list[int] = []

    def record_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        if os.fspath(path) == "/dev/null":
            inspected_flags.append(flags)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(readiness.os, "open", record_open)

    with pytest.raises(ValueError, match="regular file"):
        readiness._read_regular_file("/dev/null")

    assert inspected_flags
    assert all(flags & os.O_PATH for flags in inspected_flags)


def test_package_snapshot_is_used_for_digest_load_and_replay_after_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A package pathname mutation after capture must not create an ABA load."""

    package = _write_valid_package(tmp_path)
    _forbid_hardware(monkeypatch)
    _install_device_probes(monkeypatch)
    original_snapshot = readiness.snapshot_package
    captured_digest: str | None = None

    def snapshot_then_substitute(path: str | Path) -> object:
        nonlocal captured_digest
        snapshot = original_snapshot(path)
        captured_digest = snapshot.manifest_sha256
        package.manifest.write_text('{"substituted": true}\n', encoding="utf-8")
        return snapshot

    monkeypatch.setattr(readiness, "snapshot_package", snapshot_then_substitute)

    report = readiness.run_readiness(
        hardware_manifest=HARDWARE_MANIFEST,
        device_config=DEVICE_CONFIG,
        model_package=package.path,
        camera_device=CAMERA_PATH,
    )

    assert report.status == "pass"
    assert report.identities is not None
    assert report.identities.model_package_manifest_sha256 == captured_digest


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
    execution = readiness.ReadinessExecution(
        report=failed,
        protected_file_identities=frozenset(),
        protected_directory_identities=frozenset(),
    )
    monkeypatch.setattr(readiness_cli, "execute_readiness", lambda **kwargs: execution)
    output = tmp_path / "readiness.json"

    result = readiness_cli.main(
        [
            "--hardware-manifest",
            str(HARDWARE_MANIFEST),
            "--model-package",
            str(tmp_path / "package"),
            "--device-config",
            str(DEVICE_CONFIG),
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


def test_cli_refuses_existing_output_and_reports_only_a_stable_error_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI publication cannot overwrite data or echo an unsafe pathname."""

    passed = ReadinessReport(
        schema_version="motion-readiness-report/v1",
        status="pass",
        read_only=True,
        checks=(
            ReadinessCheck(
                check_id="motion-replay",
                status="pass",
                reason="passed",
            ),
        ),
    )
    execution = readiness.ReadinessExecution(
        report=passed,
        protected_file_identities=frozenset(),
        protected_directory_identities=frozenset(),
    )
    monkeypatch.setattr(readiness_cli, "execute_readiness", lambda **kwargs: execution)
    output = tmp_path / "private-output-name.json"
    output.write_text("sentinel\n", encoding="utf-8")

    result = readiness_cli.main(
        [
            "--hardware-manifest",
            str(HARDWARE_MANIFEST),
            "--device-config",
            str(DEVICE_CONFIG),
            "--model-package",
            str(tmp_path / "package"),
            "--camera-device",
            CAMERA_PATH,
            "--json-output",
            str(output),
        ]
    )

    emitted = json.loads(capsys.readouterr().out)
    assert result == 2
    assert output.read_text(encoding="utf-8") == "sentinel\n"
    assert emitted == {"error_code": "output-destination-exists", "status": "fail"}
    assert "private-output-name" not in json.dumps(emitted)
