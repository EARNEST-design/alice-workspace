"""Compact bounded residual dynamics for hardware-independent motion planning."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import torch
import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator
from torch import nn

from alice.contracts.blendshapes import NonEmptyString, Sha256Hex

PositiveInteger = Annotated[int, Field(ge=1)]
ResidualEnvelope = Annotated[
    float,
    Field(gt=0.0, le=1.0, allow_inf_nan=False),
]


class ResidualStateSpaceConfig(BaseModel):
    """Versioned dimensions and bounds for one residual GRU."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["residual-state-space-config/v1"]
    model_id: NonEmptyString
    affect_schema_id: NonEmptyString
    affect_dimensions: tuple[NonEmptyString, ...] = Field(min_length=1)
    controller_response_model_id: NonEmptyString
    calibration_sha256: Sha256Hex
    controller_settings_sha256: Sha256Hex
    actuator_names: tuple[NonEmptyString, ...] = Field(min_length=1)
    hidden_size: PositiveInteger
    residual_envelope: tuple[ResidualEnvelope, ...] = Field(min_length=1)
    research_status: Literal["unfitted-research-prior", "empirically-evaluated"]
    provenance: NonEmptyString

    @model_validator(mode="after")
    def validate_dimensions(self) -> ResidualStateSpaceConfig:
        if len(self.affect_dimensions) != len(set(self.affect_dimensions)):
            raise ValueError("affect dimensions must be unique")
        if len(self.actuator_names) != len(set(self.actuator_names)):
            raise ValueError("actuator names must be unique")
        if len(self.residual_envelope) != len(self.actuator_names):
            raise ValueError("residual envelope must have one value per actuator")
        return self

    @property
    def feature_size(self) -> int:
        """Return affect, intensity, anchor, response-state, and time width."""

        return len(self.affect_dimensions) + 3 * len(self.actuator_names) + 2


class ResidualStateSpace(nn.Module):
    """A single-layer GRU with an immutable per-actuator residual envelope."""

    residual_envelope: torch.Tensor

    def __init__(self, config: ResidualStateSpaceConfig) -> None:
        super().__init__()
        self.config = config
        self.gru = nn.GRU(
            input_size=config.feature_size,
            hidden_size=config.hidden_size,
            num_layers=1,
            batch_first=True,
        )
        self.residual_head = nn.Linear(
            config.hidden_size,
            len(config.actuator_names),
        )
        self.register_buffer(
            "residual_envelope",
            torch.tensor(config.residual_envelope, dtype=torch.float32),
            persistent=False,
        )

    def compose_features(
        self,
        *,
        affect: torch.Tensor,
        intensity: torch.Tensor,
        anchor_pose: torch.Tensor,
        response_position: torch.Tensor,
        response_velocity: torch.Tensor,
        elapsed_s: torch.Tensor,
    ) -> torch.Tensor:
        """Assemble every required semantic condition at every recurrent step."""

        parts = (
            ("affect", affect, len(self.config.affect_dimensions)),
            ("intensity", intensity, 1),
            ("anchor_pose", anchor_pose, len(self.config.actuator_names)),
            (
                "response_position",
                response_position,
                len(self.config.actuator_names),
            ),
            (
                "response_velocity",
                response_velocity,
                len(self.config.actuator_names),
            ),
            ("elapsed_s", elapsed_s, 1),
        )
        reference_shape = affect.shape[:2]
        reference_device = affect.device
        reference_dtype = affect.dtype
        for name, part, width in parts:
            if part.ndim != 3 or part.shape[:2] != reference_shape:
                raise ValueError(f"{name} must have shape [batch, time, {width}]")
            if part.shape[2] != width:
                raise ValueError(f"{name} must have shape [batch, time, {width}]")
            if not part.is_floating_point():
                raise ValueError(f"{name} must use a floating-point dtype")
            if part.device != reference_device or part.dtype != reference_dtype:
                raise ValueError("feature conditions must share device and dtype")
            if not bool(torch.isfinite(part).all()):
                raise ValueError(f"{name} must contain only finite values")
        if bool((elapsed_s < 0.0).any()):
            raise ValueError("elapsed_s must be non-negative")
        return torch.cat(tuple(part for _, part, _ in parts), dim=-1)

    def forward(
        self,
        features: torch.Tensor,
        hidden: torch.Tensor | None,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Advance residual dynamics and project each output through its bound."""

        if features.ndim != 3 or features.shape[-1] != self.config.feature_size:
            raise ValueError(
                f"features must have shape [batch, time, {self.config.feature_size}]"
            )
        if not features.is_floating_point() or not bool(torch.isfinite(features).all()):
            raise ValueError("features must be finite floating-point values")
        if hidden is not None:
            expected_hidden = (1, features.shape[0], self.config.hidden_size)
            if tuple(hidden.shape) != expected_hidden:
                raise ValueError(f"hidden must have shape {expected_hidden}")
            if hidden.device != features.device or hidden.dtype != features.dtype:
                raise ValueError("hidden must share feature device and dtype")
            if not bool(torch.isfinite(hidden).all()):
                raise ValueError("hidden must contain only finite values")

        recurrent, next_hidden = self.gru(features, hidden)
        raw_residual = self.residual_head(recurrent)
        envelope = self.residual_envelope.to(
            device=raw_residual.device,
            dtype=raw_residual.dtype,
        )
        return torch.tanh(raw_residual) * envelope, next_hidden


def load_residual_state_space_config(
    path: str | Path,
) -> ResidualStateSpaceConfig:
    """Load and validate a model config without importing hardware modules."""

    with Path(path).open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise ValueError("residual state-space config root must be a mapping")
    return ResidualStateSpaceConfig.model_validate(document)
