"""Behavioral tests for the versioned Alice face hardware manifest."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from alice.contracts.actuation import PoseRequest
from alice.hardware.manifest import ControllerIdentity, load_manifest

MANIFEST_PATH = Path(__file__).parents[2] / "hardware" / "alice-face-v1.yaml"


def request_for(
    *,
    actuator_name: str = "mouth_open",
    calibration_sha256: str | None = None,
    issued_monotonic_ns: int = 1_000,
    expires_monotonic_ns: int = 2_000,
) -> PoseRequest:
    manifest = load_manifest(MANIFEST_PATH)
    return PoseRequest(
        schema_version="pose-request/v1",
        request_id="request-001",
        run_id="run-001",
        hardware_id=manifest.hardware_id,
        calibration_sha256=calibration_sha256 or manifest.calibration_sha256,
        issued_monotonic_ns=issued_monotonic_ns,
        expires_monotonic_ns=expires_monotonic_ns,
        targets=(
            {
                "actuator_name": actuator_name,
                "normalized_position": 0.0,
            },
        ),
    )


def test_manifest_preserves_reviewed_controller_and_channel_identities() -> None:
    """Wrong serial, duplicate channels, or reconnecting channel 7 must fail."""

    manifest = load_manifest(MANIFEST_PATH)
    channels = [actuator.channel for actuator in manifest.actuators]

    assert manifest.controller.serial_number == "00037376"
    assert manifest.controller.command_device_path == (
        "/dev/serial/by-id/usb-Pololu_Corporation_"
        "Pololu_Mini_Maestro_12-Channel_USB_Servo_Controller_00037376-if00"
    )
    assert len(channels) == len(set(channels))
    assert 7 not in channels


def test_command_interface_role_remains_an_unmet_preflight_fact() -> None:
    manifest = load_manifest(MANIFEST_PATH)

    assert "maestro-command-interface-role-verified" in {
        item.requirement_id for item in manifest.unmet_preflight_requirements
    }


def test_manifest_rejects_duplicate_semantic_names() -> None:
    """Removing name uniqueness would make semantic lookup ambiguous."""

    manifest = load_manifest(MANIFEST_PATH)
    document = manifest.model_dump(exclude={"calibration_sha256"})
    document["actuators"][1]["name"] = document["actuators"][0]["name"]

    with pytest.raises(ValidationError, match="actuator names must be unique"):
        type(manifest).model_validate(document)


def test_normalized_zero_maps_to_every_reviewed_home_value() -> None:
    """Changing neutral interpolation must not move any actuator away from Home."""

    manifest = load_manifest(MANIFEST_PATH)
    expected_homes = {
        "neck_rotation": 6480,
        "head_tilt": 6007,
        "face_pitch": 7440,
        "lower_eyelids": 5626,
        "upper_eyelids": 6173,
        "forehead_frown": 5918,
        "mouth_open": 5059,
        "right_eye_horizontal": 6000,
        "left_mouth_corner": 6499,
        "left_eye_horizontal": 6000,
        "right_mouth_corner": 5524,
    }

    assert {
        actuator.name: actuator.target_qus(0.0) for actuator in manifest.actuators
    } == expected_homes


def test_manifest_keeps_firmware_and_software_channel_2_limits_separate() -> None:
    """Collapsing the two limits could expose the less conservative firmware range."""

    actuator = load_manifest(MANIFEST_PATH).actuator("face_pitch")

    assert actuator.channel == 2
    assert actuator.software_max_qus == 8000
    assert actuator.firmware_max_qus == 8832
    assert actuator.target_qus(1.0) == 8000


def test_channel_10_requires_linkage_inspection() -> None:
    """Dropping the jam evidence could allow preflight without linkage inspection."""

    actuator = load_manifest(MANIFEST_PATH).actuator("left_eye_horizontal")

    assert actuator.channel == 10
    assert actuator.inspection_required is True
    assert "channel-10-linkage-inspection" in actuator.preflight_requirement_ids


def test_unknown_safety_facts_are_unmet_preflight_requirements() -> None:
    """Inventing safety facts must not silently satisfy physical preconditions."""

    manifest = load_manifest(MANIFEST_PATH)
    unmet = {item.requirement_id for item in manifest.unmet_preflight_requirements}

    assert {
        "emergency-power-removal-verified",
        "electrical-current-limit-verified",
        "mechanical-clearance-verified",
        "channel-10-linkage-inspection",
    }.issubset(unmet)


def test_manifest_rejects_unknown_actuator_name() -> None:
    """Removing semantic identity validation would permit unmapped commands."""

    manifest = load_manifest(MANIFEST_PATH)

    with pytest.raises(ValueError, match="unknown actuator"):
        manifest.validate_request(
            request_for(actuator_name="unknown"), now_monotonic_ns=1_500
        )


def test_manifest_rejects_expired_request() -> None:
    """Removing freshness validation would authorize replayed commands."""

    manifest = load_manifest(MANIFEST_PATH)

    with pytest.raises(ValueError, match="expired"):
        manifest.validate_request(request_for(), now_monotonic_ns=2_000)


def test_manifest_rejects_request_issued_in_the_future() -> None:
    """Skipping the lower freshness bound would admit impossible future commands."""

    manifest = load_manifest(MANIFEST_PATH)

    with pytest.raises(ValueError, match="issued in the future"):
        manifest.validate_request(request_for(), now_monotonic_ns=999)


def test_manifest_accepts_request_at_its_issue_time_boundary() -> None:
    """The issue timestamp itself is the inclusive lower validity boundary."""

    manifest = load_manifest(MANIFEST_PATH)

    manifest.validate_request(request_for(), now_monotonic_ns=1_000)


def test_calibration_hash_binds_controller_serial_identity() -> None:
    """Changing controller identity must invalidate commands bound to calibration."""

    manifest = load_manifest(MANIFEST_PATH)
    changed = manifest.model_copy(
        update={
            "controller": ControllerIdentity(
                kind="pololu-maestro",
                serial_number="different-controller",
                command_device_path=manifest.controller.command_device_path,
            )
        }
    )

    assert changed.calibration_sha256 != manifest.calibration_sha256


def test_canonical_hash_binds_global_preflight_requirements() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    requirement = manifest.preflight_requirements[0]
    changed = manifest.model_copy(
        update={
            "preflight_requirements": (
                requirement.model_copy(update={"description": "changed safety fact"}),
                *manifest.preflight_requirements[1:],
            )
        }
    )

    assert changed.calibration_sha256 == manifest.calibration_sha256
    assert changed.canonical_sha256 != manifest.canonical_sha256


@pytest.mark.parametrize("field", ["firmware_speed", "firmware_acceleration"])
def test_calibration_hash_binds_firmware_motion_limit(field: str) -> None:
    """Changing controller-side slew limits must invalidate bound commands."""

    manifest = load_manifest(MANIFEST_PATH)
    actuator = manifest.actuators[0]
    changed_actuator = actuator.model_copy(update={field: getattr(actuator, field) + 1})
    changed = manifest.model_copy(
        update={"actuators": (changed_actuator, *manifest.actuators[1:])}
    )

    assert changed.calibration_sha256 != manifest.calibration_sha256


def test_manifest_rejects_calibration_hash_mismatch() -> None:
    """Removing calibration binding would allow targets under the wrong limits."""

    manifest = load_manifest(MANIFEST_PATH)

    with pytest.raises(ValueError, match="calibration_sha256 mismatch"):
        manifest.validate_request(
            request_for(calibration_sha256="b" * 64),
            now_monotonic_ns=1_500,
        )


def test_manifest_rejects_hardware_identity_mismatch() -> None:
    """Removing hardware binding would allow requests for another robot."""

    manifest = load_manifest(MANIFEST_PATH)
    request = request_for().model_copy(update={"hardware_id": "another-face"})

    with pytest.raises(ValueError, match="hardware_id mismatch"):
        manifest.validate_request(request, now_monotonic_ns=1_500)
