"""Deterministic CPU training for bounded residual motion dynamics."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import torch
from safetensors.torch import save_file

from alice.models.residual_state_space import (
    ResidualStateSpace,
    ResidualStateSpaceConfig,
)

DatasetSplit = Literal["train", "validation", "test"]
Disposition = Literal["keep", "discard"]


@dataclass(frozen=True, slots=True)
class ResidualDataset:
    """Small in-memory, provenance-carrying residual training dataset."""

    split: DatasetSplit | str
    dataset_id: str
    split_id: str
    input_data_reference: str
    permitted_use: str
    affect: torch.Tensor
    intensity: torch.Tensor
    anchor_pose: torch.Tensor
    response_position: torch.Tensor
    response_velocity: torch.Tensor
    elapsed_s: torch.Tensor
    target_residual: torch.Tensor
    boundary_residual: torch.Tensor

    def __post_init__(self) -> None:
        if self.split not in {"train", "validation", "test"}:
            raise ValueError("dataset split must be train, validation, or test")
        for name in (
            "dataset_id",
            "split_id",
            "input_data_reference",
            "permitted_use",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")

        sequence_shape = self.affect.shape[:2]
        sequence_tensors = (
            ("affect", self.affect),
            ("intensity", self.intensity),
            ("anchor_pose", self.anchor_pose),
            ("response_position", self.response_position),
            ("response_velocity", self.response_velocity),
            ("elapsed_s", self.elapsed_s),
            ("target_residual", self.target_residual),
        )
        if self.affect.ndim != 3 or not sequence_shape[0] or not sequence_shape[1]:
            raise ValueError("affect must contain a non-empty batch and sequence")
        for name, tensor in sequence_tensors:
            if tensor.ndim != 3 or tensor.shape[:2] != sequence_shape:
                raise ValueError(f"{name} batch/time dimensions must match affect")
            _validate_cpu_finite_tensor(name, tensor)
        if self.boundary_residual.ndim != 2:
            raise ValueError("boundary_residual must have shape [batch, actuators]")
        _validate_cpu_finite_tensor("boundary_residual", self.boundary_residual)
        if self.boundary_residual.shape[0] != sequence_shape[0]:
            raise ValueError("boundary_residual batch dimension must match affect")
        if bool((self.elapsed_s <= 0.0).any()):
            raise ValueError("elapsed_s must be positive for derivative losses")


@dataclass(frozen=True, slots=True)
class ResidualLossWeights:
    """Non-negative weights for auditable residual training objectives."""

    reconstruction: float = 1.0
    multistep_rollout: float = 0.5
    anchor_drift: float = 0.2
    boundary_continuity: float = 0.5
    realized_velocity: float = 0.05
    realized_acceleration: float = 0.02
    realized_jerk: float = 0.01

    def __post_init__(self) -> None:
        values = asdict(self).values()
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise ValueError("loss weights must be finite and non-negative")
        if not any(value > 0.0 for value in values):
            raise ValueError("at least one loss weight must be positive")


@dataclass(frozen=True, slots=True)
class ResidualTrainingConfig:
    """Resolved settings for one deterministic, offline CPU run."""

    model: ResidualStateSpaceConfig
    seed: int
    epochs: int
    learning_rate: float
    rollout_steps: int
    response_rate_per_s: float
    artifact_directory: Path
    disposition: Disposition
    note: str
    losses: ResidualLossWeights = ResidualLossWeights()

    def __post_init__(self) -> None:
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        if self.epochs < 1:
            raise ValueError("epochs must be positive")
        if not math.isfinite(self.learning_rate) or self.learning_rate <= 0.0:
            raise ValueError("learning_rate must be finite and positive")
        if self.rollout_steps < 2:
            raise ValueError("rollout_steps must be at least two")
        if not math.isfinite(self.response_rate_per_s) or self.response_rate_per_s <= 0:
            raise ValueError("response_rate_per_s must be finite and positive")
        if self.disposition not in {"keep", "discard"}:
            raise ValueError("disposition must be keep or discard")
        if not self.note.strip():
            raise ValueError("research note must not be empty")


@dataclass(frozen=True, slots=True)
class EpochLosses:
    """Scalar objective components captured after one optimizer step."""

    epoch: int
    losses: dict[str, float]


@dataclass(frozen=True, slots=True)
class TrainingResult:
    """Saved model identity and its compact reproducibility record."""

    weights_path: Path
    research_record_path: Path
    weights_sha256: str
    epochs: tuple[EpochLosses, ...]


def train_residual(
    config: ResidualTrainingConfig,
    dataset: ResidualDataset,
) -> TrainingResult:
    """Fit a residual GRU reproducibly on CPU and persist safe tensor weights."""

    if dataset.split == "test":
        raise ValueError("training on a test split is forbidden")
    _validate_dataset_for_model(config, dataset)

    weights_path = config.artifact_directory / "residual.safetensors"
    record_path = config.artifact_directory / "research-record.json"
    if weights_path.exists() or record_path.exists():
        raise FileExistsError("training artifacts already exist")

    deterministic_was_enabled = torch.are_deterministic_algorithms_enabled()
    epoch_records: list[EpochLosses] = []
    try:
        torch.use_deterministic_algorithms(True)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(config.seed)
            model = ResidualStateSpace(config.model).cpu()
            optimizer = torch.optim.Adam(
                model.parameters(),
                lr=config.learning_rate,
            )
            inputs = _cpu_float_dataset(dataset)
            features = model.compose_features(
                affect=inputs.affect,
                intensity=inputs.intensity,
                anchor_pose=inputs.anchor_pose,
                response_position=inputs.response_position,
                response_velocity=inputs.response_velocity,
                elapsed_s=inputs.elapsed_s,
            )
            for epoch in range(1, config.epochs + 1):
                optimizer.zero_grad(set_to_none=True)
                prediction, _ = model(features, hidden=None)
                terms = _loss_terms(config, inputs, prediction)
                torch.autograd.backward(terms["total"])
                optimizer.step()
                epoch_records.append(
                    EpochLosses(
                        epoch=epoch,
                        losses={
                            name: float(value.detach().cpu())
                            for name, value in terms.items()
                        },
                    )
                )
    finally:
        torch.use_deterministic_algorithms(deterministic_was_enabled)

    config.artifact_directory.mkdir(parents=True, exist_ok=True)
    weights = {
        name: tensor.detach().cpu().contiguous()
        for name, tensor in model.state_dict().items()
    }
    save_file(weights, weights_path)
    weights_sha256 = hashlib.sha256(weights_path.read_bytes()).hexdigest()
    record = _research_record(
        config=config,
        dataset=dataset,
        weights_path=weights_path,
        weights_sha256=weights_sha256,
        epochs=epoch_records,
    )
    record_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return TrainingResult(
        weights_path=weights_path,
        research_record_path=record_path,
        weights_sha256=weights_sha256,
        epochs=tuple(epoch_records),
    )


def _loss_terms(
    config: ResidualTrainingConfig,
    dataset: ResidualDataset,
    prediction: torch.Tensor,
) -> dict[str, torch.Tensor]:
    target = dataset.target_residual
    reconstruction = torch.mean((prediction - target) ** 2)
    multistep_rollout = _multistep_rollout_loss(
        prediction,
        target,
        steps=config.rollout_steps,
    )
    predicted_command = dataset.anchor_pose + prediction
    target_command = dataset.anchor_pose + target
    anchor_drift = torch.mean(
        (
            (predicted_command - predicted_command[:, :1])
            - (target_command - target_command[:, :1])
        )
        ** 2
    )
    boundary_continuity = torch.mean(
        (prediction[:, 0] - dataset.boundary_residual) ** 2
    )
    predicted_realized = _realized_positions(
        command=predicted_command,
        response_position=dataset.response_position,
        response_velocity=dataset.response_velocity,
        elapsed_s=dataset.elapsed_s,
        response_rate_per_s=config.response_rate_per_s,
    )
    target_realized = _realized_positions(
        command=target_command,
        response_position=dataset.response_position,
        response_velocity=dataset.response_velocity,
        elapsed_s=dataset.elapsed_s,
        response_rate_per_s=config.response_rate_per_s,
    )
    realized_velocity = _derivative_loss(
        predicted_realized,
        target_realized,
        dataset.elapsed_s,
        order=1,
    )
    realized_acceleration = _derivative_loss(
        predicted_realized,
        target_realized,
        dataset.elapsed_s,
        order=2,
    )
    realized_jerk = _derivative_loss(
        predicted_realized,
        target_realized,
        dataset.elapsed_s,
        order=3,
    )
    terms = {
        "reconstruction": reconstruction,
        "multistep_rollout": multistep_rollout,
        "anchor_drift": anchor_drift,
        "boundary_continuity": boundary_continuity,
        "realized_velocity": realized_velocity,
        "realized_acceleration": realized_acceleration,
        "realized_jerk": realized_jerk,
    }
    weights = asdict(config.losses)
    total = sum(
        (terms[name] * weights[name] for name in terms),
        start=prediction.new_zeros(()),
    )
    return {**terms, "total": total}


def _multistep_rollout_loss(
    prediction: torch.Tensor,
    target: torch.Tensor,
    *,
    steps: int,
) -> torch.Tensor:
    errors = prediction - target
    horizon = min(steps, errors.shape[1])
    windows = tuple(
        torch.cumsum(errors[:, start : start + horizon], dim=1)
        for start in range(errors.shape[1] - horizon + 1)
    )
    return torch.mean(torch.stack(tuple(window**2 for window in windows)))


def _realized_positions(
    *,
    command: torch.Tensor,
    response_position: torch.Tensor,
    response_velocity: torch.Tensor,
    elapsed_s: torch.Tensor,
    response_rate_per_s: float,
) -> torch.Tensor:
    projected = response_position + response_velocity * elapsed_s
    blend = 1.0 - torch.exp(-response_rate_per_s * elapsed_s)
    return projected + blend * (command - projected)


def _derivative_loss(
    prediction: torch.Tensor,
    target: torch.Tensor,
    elapsed_s: torch.Tensor,
    *,
    order: int,
) -> torch.Tensor:
    predicted_derivative = prediction
    target_derivative = target
    intervals = elapsed_s
    for _ in range(order):
        if predicted_derivative.shape[1] < 2:
            return prediction.new_zeros(())
        intervals = intervals[:, 1:]
        predicted_derivative = (
            predicted_derivative[:, 1:] - predicted_derivative[:, :-1]
        ) / intervals
        target_derivative = (
            target_derivative[:, 1:] - target_derivative[:, :-1]
        ) / intervals
    return torch.mean((predicted_derivative - target_derivative) ** 2)


def _validate_dataset_for_model(
    config: ResidualTrainingConfig,
    dataset: ResidualDataset,
) -> None:
    actuator_count = len(config.model.actuator_names)
    expected_widths = {
        "affect": len(config.model.affect_dimensions),
        "intensity": 1,
        "anchor_pose": actuator_count,
        "response_position": actuator_count,
        "response_velocity": actuator_count,
        "elapsed_s": 1,
        "target_residual": actuator_count,
    }
    for name, width in expected_widths.items():
        if getattr(dataset, name).shape[2] != width:
            raise ValueError(f"{name} width must be {width}")
    if dataset.boundary_residual.shape[1] != actuator_count:
        raise ValueError(f"boundary_residual width must be {actuator_count}")
    if dataset.affect.shape[1] < 2:
        raise ValueError("training sequences must contain at least two steps")


def _cpu_float_dataset(dataset: ResidualDataset) -> ResidualDataset:
    values = {
        name: getattr(dataset, name).detach().to(device="cpu", dtype=torch.float32)
        for name in (
            "affect",
            "intensity",
            "anchor_pose",
            "response_position",
            "response_velocity",
            "elapsed_s",
            "target_residual",
            "boundary_residual",
        )
    }
    return ResidualDataset(
        split=dataset.split,
        dataset_id=dataset.dataset_id,
        split_id=dataset.split_id,
        input_data_reference=dataset.input_data_reference,
        permitted_use=dataset.permitted_use,
        **values,
    )


def _validate_cpu_finite_tensor(name: str, tensor: torch.Tensor) -> None:
    if tensor.device.type != "cpu":
        raise ValueError(f"{name} must be a CPU tensor")
    if not tensor.is_floating_point():
        raise ValueError(f"{name} must use a floating-point dtype")
    if not bool(torch.isfinite(tensor).all()):
        raise ValueError(f"{name} must contain only finite values")


def _research_record(
    *,
    config: ResidualTrainingConfig,
    dataset: ResidualDataset,
    weights_path: Path,
    weights_sha256: str,
    epochs: list[EpochLosses],
) -> dict[str, object]:
    return {
        "schema_version": "residual-training-record/v1",
        "model": config.model.model_dump(mode="json"),
        "training": {
            "epochs": config.epochs,
            "learning_rate": config.learning_rate,
            "rollout_steps": config.rollout_steps,
            "response_rate_per_s": config.response_rate_per_s,
            "loss_weights": asdict(config.losses),
        },
        "run": {
            "seed": config.seed,
            "device": "cpu",
            "disposition": config.disposition,
            "note": config.note,
        },
        "dataset": {
            "dataset_id": dataset.dataset_id,
            "split": dataset.split,
            "split_id": dataset.split_id,
            "input_data_reference": dataset.input_data_reference,
            "permitted_use": dataset.permitted_use,
        },
        "artifact": {
            "format": "safetensors",
            "path": weights_path.name,
            "weights_sha256": weights_sha256,
        },
        "epochs": [asdict(epoch) for epoch in epochs],
    }
