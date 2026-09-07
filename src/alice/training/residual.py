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
from alice.motion.controller_response import ControllerResponseConfig

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
    controller_response_config: ControllerResponseConfig
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
        if (
            self.controller_response_config.model_id
            != self.model.controller_response_model_id
        ):
            raise ValueError("training controller-response model identity mismatch")
        if (
            self.controller_response_config.calibration_sha256
            != self.model.calibration_sha256
        ):
            raise ValueError("training controller calibration identity mismatch")
        if (
            tuple(a.actuator_name for a in self.controller_response_config.actuators)
            != self.model.actuator_names
        ):
            raise ValueError("training controller actuator identities mismatch")
        if self.disposition not in {"keep", "discard"}:
            raise ValueError("disposition must be keep or discard")
        if not self.note.strip():
            raise ValueError("research note must not be empty")


@dataclass(frozen=True, slots=True)
class EpochLosses:
    """Scalar objective components used for one optimizer step."""

    epoch: int
    losses: dict[str, float]


@dataclass(frozen=True, slots=True)
class TrainingResult:
    """Saved model identity and its compact reproducibility record."""

    weights_path: Path
    research_record_path: Path
    weights_sha256: str
    epochs: tuple[EpochLosses, ...]


@dataclass(frozen=True, slots=True)
class ResidualRollout:
    """Closed-loop residual and differentiable controller-response trajectory."""

    residuals: torch.Tensor
    realized_position: torch.Tensor
    realized_velocity: torch.Tensor
    hidden: torch.Tensor


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

    deterministic_debug_mode = torch.get_deterministic_debug_mode()
    cpu_rng_state = torch.random.get_rng_state()
    epoch_records: list[EpochLosses] = []
    try:
        torch.set_deterministic_debug_mode("error")
        torch.default_generator.manual_seed(config.seed)
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
            terms = _loss_terms(config, inputs, prediction, model=model)
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
        torch.random.set_rng_state(cpu_rng_state)
        torch.set_deterministic_debug_mode(deterministic_debug_mode)

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
    *,
    model: ResidualStateSpace,
) -> dict[str, torch.Tensor]:
    rollout = recurrent_rollout(
        model,
        dataset,
        steps=dataset.affect.shape[1],
        controller_response_config=config.controller_response_config,
    )
    target_realized, _ = _target_response_rollout(
        dataset,
        steps=dataset.affect.shape[1],
        controller_response_config=config.controller_response_config,
    )
    terms = residual_objective_terms(
        prediction=prediction,
        target_residual=dataset.target_residual,
        anchor_pose=dataset.anchor_pose,
        boundary_residual=dataset.boundary_residual,
        predicted_realized=rollout.realized_position,
        target_realized=target_realized,
        elapsed_s=dataset.elapsed_s,
        rollout_steps=min(config.rollout_steps, dataset.affect.shape[1]),
    )
    weights = asdict(config.losses)
    total = sum(
        (terms[name] * weights[name] for name in terms),
        start=prediction.new_zeros(()),
    )
    return {**terms, "total": total}


def recurrent_rollout(
    model: ResidualStateSpace,
    dataset: ResidualDataset,
    *,
    steps: int,
    controller_response_config: ControllerResponseConfig,
) -> ResidualRollout:
    """Roll forward while feeding each predicted response into the next GRU step."""

    if steps < 1 or steps > dataset.affect.shape[1]:
        raise ValueError("rollout steps must fit inside the dataset sequence")
    position = dataset.response_position[:, 0]
    velocity = dataset.response_velocity[:, 0]
    hidden: torch.Tensor | None = None
    residuals: list[torch.Tensor] = []
    positions: list[torch.Tensor] = []
    velocities: list[torch.Tensor] = []
    for index in range(steps):
        features = model.compose_features(
            affect=dataset.affect[:, index : index + 1],
            intensity=dataset.intensity[:, index : index + 1],
            anchor_pose=dataset.anchor_pose[:, index : index + 1],
            response_position=position.unsqueeze(1),
            response_velocity=velocity.unsqueeze(1),
            elapsed_s=dataset.elapsed_s[:, index : index + 1],
        )
        step_residual, hidden = model(features, hidden)
        residual = step_residual[:, 0]
        command = dataset.anchor_pose[:, index] + residual
        position, velocity = _response_step(
            command=command,
            position=position,
            velocity=velocity,
            elapsed_s=dataset.elapsed_s[:, index],
            controller_response_config=controller_response_config,
        )
        residuals.append(residual)
        positions.append(position)
        velocities.append(velocity)
    if hidden is None:
        raise RuntimeError("non-empty rollout did not produce recurrent state")
    return ResidualRollout(
        residuals=torch.stack(residuals, dim=1),
        realized_position=torch.stack(positions, dim=1),
        realized_velocity=torch.stack(velocities, dim=1),
        hidden=hidden,
    )


def residual_objective_terms(
    *,
    prediction: torch.Tensor,
    target_residual: torch.Tensor,
    anchor_pose: torch.Tensor,
    boundary_residual: torch.Tensor,
    predicted_realized: torch.Tensor,
    target_realized: torch.Tensor,
    elapsed_s: torch.Tensor,
    rollout_steps: int,
) -> dict[str, torch.Tensor]:
    """Compute independently testable command, boundary, and realized losses."""

    if rollout_steps < 1 or rollout_steps > prediction.shape[1]:
        raise ValueError("rollout_steps must fit inside the prediction sequence")
    predicted_command = anchor_pose + prediction
    target_command = anchor_pose + target_residual
    return {
        "reconstruction": torch.mean((prediction - target_residual) ** 2),
        "multistep_rollout": torch.mean(
            (predicted_realized[:, :rollout_steps] - target_realized[:, :rollout_steps])
            ** 2
        ),
        "anchor_drift": torch.mean(
            (
                (predicted_command - predicted_command[:, :1])
                - (target_command - target_command[:, :1])
            )
            ** 2
        ),
        "boundary_continuity": torch.mean((prediction[:, 0] - boundary_residual) ** 2),
        "realized_velocity": _derivative_loss(
            predicted_realized,
            target_realized,
            elapsed_s,
            order=1,
        ),
        "realized_acceleration": _derivative_loss(
            predicted_realized,
            target_realized,
            elapsed_s,
            order=2,
        ),
        "realized_jerk": _derivative_loss(
            predicted_realized,
            target_realized,
            elapsed_s,
            order=3,
        ),
    }


def _target_response_rollout(
    dataset: ResidualDataset,
    *,
    steps: int,
    controller_response_config: ControllerResponseConfig,
) -> tuple[torch.Tensor, torch.Tensor]:
    position = dataset.response_position[:, 0]
    velocity = dataset.response_velocity[:, 0]
    positions: list[torch.Tensor] = []
    velocities: list[torch.Tensor] = []
    for index in range(steps):
        command = dataset.anchor_pose[:, index] + dataset.target_residual[:, index]
        position, velocity = _response_step(
            command=command,
            position=position,
            velocity=velocity,
            elapsed_s=dataset.elapsed_s[:, index],
            controller_response_config=controller_response_config,
        )
        positions.append(position)
        velocities.append(velocity)
    return torch.stack(positions, dim=1), torch.stack(velocities, dim=1)


def _response_step(
    *,
    command: torch.Tensor,
    position: torch.Tensor,
    velocity: torch.Tensor,
    elapsed_s: torch.Tensor,
    controller_response_config: ControllerResponseConfig,
) -> tuple[torch.Tensor, torch.Tensor]:
    velocities = position.new_tensor(
        [a.max_velocity_per_s for a in controller_response_config.actuators]
    )
    accelerations = position.new_tensor(
        [a.max_acceleration_per_s2 for a in controller_response_config.actuators]
    )
    dt = elapsed_s.expand_as(position)
    desired_velocity = torch.clamp((command - position) / dt, -velocities, velocities)
    velocity_delta = torch.clamp(
        desired_velocity - velocity,
        -accelerations * dt,
        accelerations * dt,
    )
    next_velocity = velocity + velocity_delta
    step = next_velocity * dt
    remaining = command - position
    step = torch.where(step.abs() > remaining.abs(), remaining, step)
    next_position = torch.clamp(position + step, -1.0, 1.0)
    next_velocity = torch.where(
        step == remaining, torch.zeros_like(next_velocity), next_velocity
    )
    return next_position, next_velocity


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
    sequence_steps = dataset.affect.shape[1]
    if config.losses.multistep_rollout > 0.0 and sequence_steps < config.rollout_steps:
        raise ValueError(
            f"multistep_rollout loss requires at least {config.rollout_steps} positions"
        )
    derivative_requirements = (
        ("realized_velocity", config.losses.realized_velocity, 2),
        ("realized_acceleration", config.losses.realized_acceleration, 3),
        ("realized_jerk", config.losses.realized_jerk, 4),
    )
    for loss_name, weight, minimum in derivative_requirements:
        if weight > 0.0 and sequence_steps < minimum:
            raise ValueError(f"{loss_name} loss requires at least {minimum} positions")


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
        "schema_version": "residual-training-record/v2",
        "model": config.model.model_dump(mode="json"),
        "training": {
            "epochs": config.epochs,
            "learning_rate": config.learning_rate,
            "rollout_steps": config.rollout_steps,
            "controller_settings_sha256": (
                config.controller_response_config.controller_settings_sha256
            ),
            "controller_response_sha256": (
                config.controller_response_config.response_sha256
            ),
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
