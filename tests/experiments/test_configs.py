from pathlib import Path

import yaml  # type: ignore[import-untyped]

from alice.experiments.config import PassiveCaptureConfig


def test_tracked_passive_configs_use_typed_setup_and_retention_schema() -> None:
    for path in (
        Path("config/experiments/passive-alice-face.example.yaml"),
        Path("config/experiments/passive-alice-face-pilot.yaml"),
    ):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        config = PassiveCaptureConfig.model_validate(payload)

        assert config.setup.stable_camera_identity
        assert config.setup.alice_full_face_confirmed is True
        assert config.setup.participant_exclusion_confirmed is True
        assert config.retention_policy.mode == "derived_observations_only"
        assert config.repeatability_thresholds is not None
