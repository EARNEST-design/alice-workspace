from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import torch
from safetensors.torch import load_file

from alice.models.residual_state_space import ResidualStateSpaceConfig
from alice.training.residual import (
    ResidualDataset,
    ResidualLossWeights,
    ResidualTrainingConfig,
    train_residual,
)


def _model_config() -> ResidualStateSpaceConfig:
    return ResidualStateSpaceConfig(
        schema_version="residual-state-space-config/v1",
        model_id="tiny-residual-v1",
        affect_schema_id="affect-vector/v1",
        affect_dimensions=("valence", "arousal"),
        controller_response_model_id="controller-response-test-v1",
        calibration_sha256="a" * 64,
        controller_settings_sha256="b" * 64,
        actuator_names=("mouth_open", "neck_rotation"),
        hidden_size=4,
        residual_envelope=(0.08, 0.12),
    )


def _dataset(*, split: str = "train") -> ResidualDataset:
    affect = torch.tensor(
        [
            [[0.1, 0.2], [0.2, 0.1], [0.3, 0.0], [0.2, -0.1]],
            [[-0.2, 0.3], [-0.1, 0.2], [0.0, 0.1], [0.1, 0.0]],
        ],
        dtype=torch.float32,
    )
    intensity = torch.tensor(
        [[[0.5], [0.6], [0.7], [0.6]], [[0.4], [0.5], [0.6], [0.5]]],
        dtype=torch.float32,
    )
    anchor = torch.tensor(
        [
            [[0.0, 0.1], [0.01, 0.1], [0.02, 0.09], [0.01, 0.08]],
            [[0.0, -0.1], [-0.01, -0.09], [-0.02, -0.08], [-0.01, -0.07]],
        ],
        dtype=torch.float32,
    )
    target = torch.tensor(
        [
            [[0.0, 0.0], [0.01, -0.01], [0.02, -0.02], [0.01, -0.01]],
            [[0.0, 0.0], [-0.01, 0.01], [-0.02, 0.02], [-0.01, 0.01]],
        ],
        dtype=torch.float32,
    )
    return ResidualDataset(
        split=split,
        dataset_id="tiny-derived-fixture-v1",
        split_id=f"tiny-{split}-split-v1",
        input_data_reference="tests/training deterministic synthetic fixture",
        permitted_use="automated software verification only",
        affect=affect,
        intensity=intensity,
        anchor_pose=anchor,
        response_position=anchor * 0.9,
        response_velocity=torch.zeros_like(anchor),
        elapsed_s=torch.full((2, 4, 1), 0.1),
        target_residual=target,
        boundary_residual=target[:, 0],
    )


def _training_config(path: Path, *, seed: int = 19) -> ResidualTrainingConfig:
    return ResidualTrainingConfig(
        model=_model_config(),
        seed=seed,
        epochs=2,
        learning_rate=0.01,
        rollout_steps=3,
        response_rate_per_s=4.0,
        artifact_directory=path,
        disposition="keep",
        note="Tiny CPU reproducibility fixture; not evidence for model selection.",
        losses=ResidualLossWeights(
            reconstruction=1.0,
            multistep_rollout=0.4,
            anchor_drift=0.2,
            boundary_continuity=0.3,
            realized_velocity=0.1,
            realized_acceleration=0.05,
            realized_jerk=0.02,
        ),
    )


def test_fixed_seed_training_repeats_on_cpu(tmp_path: Path) -> None:
    """Using global or accelerator RNG state would change the saved weights."""

    first = train_residual(_training_config(tmp_path / "first"), _dataset())
    second = train_residual(_training_config(tmp_path / "second"), _dataset())

    assert first.weights_sha256 == second.weights_sha256
    first_weights = load_file(first.weights_path, device="cpu")
    second_weights = load_file(second.weights_path, device="cpu")
    assert first_weights.keys() == second_weights.keys()
    assert all(
        torch.equal(first_weights[name], second_weights[name]) for name in first_weights
    )


def test_training_refuses_test_split_before_writing(tmp_path: Path) -> None:
    """A held-out test partition must never become optimizer input."""

    artifact_directory = tmp_path / "forbidden"
    with pytest.raises(ValueError, match="test split"):
        train_residual(
            _training_config(artifact_directory),
            _dataset(split="test"),
        )

    assert not artifact_directory.exists()


def test_training_records_all_objectives_and_compact_provenance(
    tmp_path: Path,
) -> None:
    """Omitting a required objective or provenance field would make the run opaque."""

    result = train_residual(_training_config(tmp_path / "record"), _dataset())
    record = json.loads(result.research_record_path.read_text(encoding="utf-8"))

    assert result.weights_path.suffix == ".safetensors"
    assert result.weights_path.is_file()
    assert record["artifact"]["weights_sha256"] == result.weights_sha256
    assert record["run"] == {
        "seed": 19,
        "device": "cpu",
        "disposition": "keep",
        "note": "Tiny CPU reproducibility fixture; not evidence for model selection.",
    }
    assert record["dataset"] == {
        "dataset_id": "tiny-derived-fixture-v1",
        "split": "train",
        "split_id": "tiny-train-split-v1",
        "input_data_reference": "tests/training deterministic synthetic fixture",
        "permitted_use": "automated software verification only",
    }
    required = {
        "reconstruction",
        "multistep_rollout",
        "anchor_drift",
        "boundary_continuity",
        "realized_velocity",
        "realized_acceleration",
        "realized_jerk",
        "total",
    }
    assert set(record["epochs"][-1]["losses"]) == required
    assert all(value >= 0.0 for value in record["epochs"][-1]["losses"].values())


def test_model_and_trainer_import_without_hardware_modules() -> None:
    """Offline ML imports must not initialize or even load hardware adapters."""

    code = """
import sys
import alice.models.residual_state_space
import alice.training.residual
assert not any(name.startswith('alice.hardware') for name in sys.modules)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
