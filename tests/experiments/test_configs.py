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
