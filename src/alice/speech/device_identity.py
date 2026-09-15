"""Shared read-only device identities; no ML, camera or serial imports."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from alice.contracts.blendshapes import NonEmptyString

_CONTROLLER_NAME = re.compile(
    r"^usb-Pololu_Corporation_Pololu_Mini_Maestro_12-Channel_USB_"
    r"Servo_Controller_(?P<serial>[A-Za-z0-9]+)-if(?P<interface>[0-9]{2})$"
)
_CAMERA_NAME = re.compile(r"^usb-[^/\s]+-video-index(?P<index>[0-9]+)$")
_TTY_NAME = re.compile(r"^ttyACM[0-9]+$")
_VIDEO_NAME = re.compile(r"^video[0-9]+$")
_CONTROLLER_ROOT = Path("/dev/serial/by-id")
_CAMERA_ROOT = Path("/dev/v4l/by-id")


@dataclass(frozen=True, slots=True)
class FilesystemIdentity:
    """Stable identity of one snapshotted filesystem object."""

    device: int
    inode: int


class ControllerDeviceExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_path: str
    usb_vendor_id: str
    usb_product_id: str
    usb_serial: str
    usb_interface: str


class CameraDeviceExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_path: str
    usb_vendor_id: str
    usb_product_id: str
    usb_serial: str
    usb_interface: str
    video_index: int


class ReadinessDeviceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["motion-readiness-device-config/v1"]
    controller: ControllerDeviceExpectation
    camera: CameraDeviceExpectation


@dataclass(frozen=True, slots=True)
class DeviceNodeSnapshot:
    """Read-only kernel and USB metadata for a device node."""

    kind: str
    stable_path: str
    node_name: str
    node_kind: str
    node_identity: FilesystemIdentity
    rdev_major: int
    rdev_minor: int
    kernel_major: int
    kernel_minor: int
    usb_vendor_id: str
    usb_product_id: str
    usb_serial: str
    usb_interface: str
    device_index: int | None


class StableDeviceIdentity(BaseModel):
    """Non-opening identity evidence for one stable Linux device selector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["controller", "camera"]
    stable_path: str
    stable_id: str
    resolved_device_name: str
    node_device: int
    node_inode: int
    rdev_major: int
    rdev_minor: int


def inspect_controller_identity(
    stable_path: str | Path,
    expectation: ControllerDeviceExpectation,
    *,
    sys_tty_root: Path = Path("/sys/class/tty"),
    snapshot_device=None,
) -> StableDeviceIdentity:
    """Verify a Maestro command-port by-id link and its read-only sysfs identity."""

    stable = _require_canonical_device_path(
        stable_path,
        expected=expectation.stable_path,
        root=_CONTROLLER_ROOT,
    )
    match = _CONTROLLER_NAME.fullmatch(stable.name)
    if match is None:
        raise ValueError("controller canonical identity is invalid")
    snapshot = (snapshot_device or _snapshot_device)(stable, sys_tty_root, "controller")
    _validate_device_snapshot(
        snapshot,
        vendor=expectation.usb_vendor_id,
        product=expectation.usb_product_id,
        serial_number=expectation.usb_serial,
        interface=expectation.usb_interface,
        device_index=None,
    )
    if (
        match.group("serial") != expectation.usb_serial
        or match.group("interface") != expectation.usb_interface
    ):
        raise ValueError("controller canonical USB identity is invalid")
    return StableDeviceIdentity(
        kind="controller",
        stable_path=str(stable),
        stable_id=(f"pololu-maestro:{snapshot.usb_serial}:if{snapshot.usb_interface}"),
        resolved_device_name=snapshot.node_name,
        node_device=snapshot.node_identity.device,
        node_inode=snapshot.node_identity.inode,
        rdev_major=snapshot.rdev_major,
        rdev_minor=snapshot.rdev_minor,
    )


def inspect_camera_identity(
    stable_path: str | Path,
    expectation: CameraDeviceExpectation,
    *,
    sys_video_root: Path = Path("/sys/class/video4linux"),
    snapshot_device=None,
) -> StableDeviceIdentity:
    """Verify a stable V4L2 capture link and sysfs membership without opening it."""

    stable = _require_canonical_device_path(
        stable_path,
        expected=expectation.stable_path,
        root=_CAMERA_ROOT,
    )
    match = _CAMERA_NAME.fullmatch(stable.name)
    if match is None:
        raise ValueError("camera canonical identity is invalid")
    snapshot = (snapshot_device or _snapshot_device)(stable, sys_video_root, "camera")
    _validate_device_snapshot(
        snapshot,
        vendor=expectation.usb_vendor_id,
        product=expectation.usb_product_id,
        serial_number=expectation.usb_serial,
        interface=expectation.usb_interface,
        device_index=expectation.video_index,
    )
    if int(match.group("index")) != expectation.video_index:
        raise ValueError("camera device index identity mismatch")
    return StableDeviceIdentity(
        kind="camera",
        stable_path=str(stable),
        stable_id=stable.name,
        resolved_device_name=snapshot.node_name,
        node_device=snapshot.node_identity.device,
        node_inode=snapshot.node_identity.inode,
        rdev_major=snapshot.rdev_major,
        rdev_minor=snapshot.rdev_minor,
    )


def load_readiness_device_config(path: str | Path) -> ReadinessDeviceConfig:
    """Load a reviewed device expectation from a real regular file."""

    config, _ = _snapshot_readiness_device_config(path)
    return config


def _snapshot_readiness_device_config(
    path: str | Path,
) -> tuple[ReadinessDeviceConfig, FilesystemIdentity]:
    payload, identity = _read_regular_file(path)
    document = yaml.safe_load(payload)
    if not isinstance(document, dict):
        raise ValueError("readiness device config is invalid")
    return ReadinessDeviceConfig.model_validate(document), identity


def _require_canonical_device_path(
    path: str | Path,
    *,
    expected: str,
    root: Path,
) -> Path:
    candidate = Path(path)
    if (
        str(candidate) != expected
        or not candidate.is_absolute()
        or candidate.parent != root
        or ".." in candidate.parts
    ):
        raise ValueError("device path does not match its exact canonical identity")
    return candidate


def _snapshot_device(
    stable: Path,
    sys_class_root: Path,
    kind: str,
) -> DeviceNodeSnapshot:
    """Read device metadata without opening the serial or video node."""

    link_before = os.lstat(stable)
    if not stat.S_ISLNK(link_before.st_mode):
        raise ValueError("canonical device identity must be a symlink")
    target_before = os.readlink(stable)
    target = Path(os.path.normpath(stable.parent / target_before))
    expected_pattern = _TTY_NAME if kind == "controller" else _VIDEO_NAME
    if target.parent != Path("/dev") or expected_pattern.fullmatch(target.name) is None:
        raise ValueError("canonical device link target is invalid")
    node_before = os.stat(target, follow_symlinks=False)
    node_kind = "character" if stat.S_ISCHR(node_before.st_mode) else "other"

    kernel_text = _read_sysfs_text(sys_class_root / target.name / "dev")
    try:
        kernel_major, kernel_minor = (int(value) for value in kernel_text.split(":", 1))
    except (TypeError, ValueError) as error:
        raise ValueError("kernel device number is invalid") from error
    interface_path = (sys_class_root / target.name / "device").resolve(strict=True)
    if sys_class_root.is_absolute() and str(sys_class_root).startswith("/sys/"):
        if not interface_path.is_relative_to(Path("/sys/devices")):
            raise ValueError("device sysfs ancestry is invalid")
    usb_interface = _read_sysfs_text(interface_path / "bInterfaceNumber").zfill(2)
    usb_parent = _find_usb_parent(interface_path)
    vendor = _read_sysfs_text(usb_parent / "idVendor").lower()
    product = _read_sysfs_text(usb_parent / "idProduct").lower()
    serial_number = _read_sysfs_text(usb_parent / "serial")

    link_after = os.lstat(stable)
    target_after = os.readlink(stable)
    node_after = os.stat(target, follow_symlinks=False)
    if (
        _stat_identity(link_before) != _stat_identity(link_after)
        or target_before != target_after
        or _stat_identity(node_before) != _stat_identity(node_after)
        or node_before.st_mode != node_after.st_mode
        or node_before.st_rdev != node_after.st_rdev
    ):
        raise ValueError("device identity changed during metadata snapshot")
    camera_match = _CAMERA_NAME.fullmatch(stable.name)
    if kind == "camera" and camera_match is None:
        raise ValueError("camera canonical identity is invalid")
    device_index = int(camera_match.group("index")) if camera_match else None
    return DeviceNodeSnapshot(
        kind=kind,
        stable_path=str(stable),
        node_name=target.name,
        node_kind=node_kind,
        node_identity=_stat_identity(node_after),
        rdev_major=os.major(node_after.st_rdev),
        rdev_minor=os.minor(node_after.st_rdev),
        kernel_major=kernel_major,
        kernel_minor=kernel_minor,
        usb_vendor_id=vendor,
        usb_product_id=product,
        usb_serial=serial_number,
        usb_interface=usb_interface,
        device_index=device_index,
    )


def _find_usb_parent(interface_path: Path) -> Path:
    for ancestor in (interface_path, *interface_path.parents):
        try:
            _read_sysfs_text(ancestor / "idVendor")
            _read_sysfs_text(ancestor / "idProduct")
            _read_sysfs_text(ancestor / "serial")
        except (OSError, ValueError):
            continue
        return ancestor
    raise ValueError("USB parent identity is unavailable")


def _validate_device_snapshot(
    snapshot: DeviceNodeSnapshot,
    *,
    vendor: str,
    product: str,
    serial_number: str,
    interface: str,
    device_index: int | None,
) -> None:
    if snapshot.node_kind != "character":
        raise ValueError("resolved device node is not a character device")
    if (snapshot.rdev_major, snapshot.rdev_minor) != (
        snapshot.kernel_major,
        snapshot.kernel_minor,
    ):
        raise ValueError("character and kernel device numbers do not match")
    if (
        snapshot.usb_vendor_id.lower(),
        snapshot.usb_product_id.lower(),
        snapshot.usb_serial,
        snapshot.usb_interface.zfill(2),
    ) != (vendor.lower(), product.lower(), serial_number, interface.zfill(2)):
        raise ValueError("USB identity does not match reviewed configuration")
    if snapshot.device_index != device_index:
        raise ValueError("device index identity mismatch")


def _read_sysfs_text(path: Path) -> str:
    payload, _ = _read_regular_file(path)
    return payload.decode("ascii").strip()


def _read_regular_file(path: str | Path) -> tuple[bytes, FilesystemIdentity]:
    descriptor = _open_verified_regular_file(Path(path))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("input must be a real regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            _stat_identity(before) != _stat_identity(after)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            raise ValueError("input changed during snapshot")
        return b"".join(chunks), _stat_identity(after)
    finally:
        os.close(descriptor)


def _open_verified_regular_file(path: str | Path) -> int:
    """Acquire a readable descriptor only after an O_PATH type inspection."""

    try:
        path_flags = os.O_PATH
    except AttributeError as error:
        raise ValueError("secure regular file inspection is unavailable") from error
    path_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    probe = os.open(path, path_flags)
    try:
        expected = os.fstat(probe)
        if not stat.S_ISREG(expected.st_mode):
            raise ValueError("input must be a real regular file")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(f"/proc/self/fd/{probe}", flags)
        try:
            actual = os.fstat(descriptor)
            if _stat_identity(actual) != _stat_identity(expected):
                raise ValueError("input identity changed before snapshot")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise
    finally:
        os.close(probe)


def _stat_identity(metadata: os.stat_result) -> FilesystemIdentity:
    return FilesystemIdentity(device=metadata.st_dev, inode=metadata.st_ino)


class LinuxUsbIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    serial_number: NonEmptyString
    interface_number: NonEmptyString
    resolved_tty: NonEmptyString


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
