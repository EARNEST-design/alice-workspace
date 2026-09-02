from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from alice.experiments.config import PassiveCaptureConfig


def test_tracked_passive_configs_use_typed_setup_and_retention_schema() -> None:
    path = Path("config/experiments/passive-alice-face-pilot.yaml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = PassiveCaptureConfig.model_validate(payload)

    assert config.setup.camera_id == config.camera_id
    assert config.setup.stable_camera_identity
    assert config.setup.alice_full_face_confirmed is True
    assert config.setup.participant_exclusion_confirmed is True
    assert config.retention_policy.mode == "derived_observations_only"
    assert config.repeatability_thresholds is not None


def test_example_config_cannot_be_executed_with_required_placeholders() -> None:
    path = Path("config/experiments/passive-alice-face.example.yaml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))

    with pytest.raises(ValueError, match="REQUIRED placeholder"):
        PassiveCaptureConfig.model_validate(payload)


def test_c920_comparison_config_uses_exact_inventoried_camera_and_locked_mode() -> None:
    """Falling back to a transient node or C525 would invalidate comparison."""
    path = Path("config/experiments/passive-alice-face-c920-comparison.yaml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = PassiveCaptureConfig.model_validate(payload)

    assert config.camera_id == (
        "usb-046d_HD_Pro_Webcam_C920_BF4BEEAF-video-index0"
    )
    assert config.camera_device == f"/dev/v4l/by-id/{config.camera_id}"
    assert (config.requested_width, config.requested_height, config.requested_fps) == (
        1280,
        720,
        10,
    )
    assert config.setup.focus.state.value == "confirmed"
    assert config.setup.exposure.state.value == "confirmed"
    assert config.retain_frames is False
