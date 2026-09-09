"""Strict provenance for the differentiable, unfitted training response surrogate."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StringConstraints,
    model_validator,
)

from alice.contracts.blendshapes import Sha256Hex
from alice.motion.controller_response import ControllerResponseConfig

BackendIdentifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]
# Match the reference config's positive finite limits, without optimizer bounds.
BackendPositiveRate = Annotated[StrictFloat, Field(gt=0.0, allow_inf_nan=False)]


class TrainingResponseActuator(BaseModel):
    """Ordered resolved limits actually consumed by the loss backend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actuator_name: BackendIdentifier
    max_velocity_per_s: BackendPositiveRate
    max_acceleration_per_s2: BackendPositiveRate


class TrainingResponseBackend(BaseModel):
    """An explicit numerical identity; never inferred from a reference hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["training-response-backend/v1"]
    backend_id: Literal["bounded-euler-surrogate/v1"]
    source_controller_response_sha256: Sha256Hex
    actuators: tuple[TrainingResponseActuator, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_actuators(self) -> Self:
        names = tuple(actuator.actuator_name for actuator in self.actuators)
        if len(names) != len(set(names)):
            raise ValueError("training response backend actuator names must be unique")
        return self


def bounded_euler_backend(config: ControllerResponseConfig) -> TrainingResponseBackend:
    """Describe bounded-euler-surrogate/v1 from its exact source configuration."""

    return TrainingResponseBackend(
        schema_version="training-response-backend/v1",
        backend_id="bounded-euler-surrogate/v1",
        source_controller_response_sha256=config.response_sha256,
        actuators=tuple(
            TrainingResponseActuator(
                actuator_name=actuator.actuator_name,
                max_velocity_per_s=actuator.max_velocity_per_s,
                max_acceleration_per_s2=actuator.max_acceleration_per_s2,
            )
            for actuator in config.actuators
        ),
    )
