"""Deterministic, hardware-independent Maestro/servo response estimation."""

from __future__ import annotations

import math
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.contracts.motion import TargetUpdate

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
FinitePositiveFloat = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
NormalizedPosition = Annotated[
    float,
    Field(ge=-1.0, le=1.0, allow_inf_nan=False),
]


class ControllerLimitMode(StrEnum):
    """How a controller setting is resolved to a physical-response estimate."""

    CONTROLLER_LIMITED = "controller-limited"
    ZERO_SETTING_RESPONSE_ESTIMATE = "zero-setting-response-estimate"


class ActuatorResponseParameters(BaseModel):
    """Response limits resolved for one semantic actuator identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actuator_name: NonEmptyString
    firmware_speed_setting: Annotated[int, Field(ge=0)]
    firmware_acceleration_setting: Annotated[int, Field(ge=0)]
    speed_mode: ControllerLimitMode
    acceleration_mode: ControllerLimitMode
    max_velocity_per_s: FinitePositiveFloat
    max_acceleration_per_s2: FinitePositiveFloat
    provenance: NonEmptyString

    @model_validator(mode="after")
    def validate_setting_modes(self) -> ActuatorResponseParameters:
        self._validate_mode(
            setting=self.firmware_speed_setting,
            mode=self.speed_mode,
            setting_name="speed",
        )
        self._validate_mode(
            setting=self.firmware_acceleration_setting,
            mode=self.acceleration_mode,
            setting_name="acceleration",
        )
        return self

    @staticmethod
    def _validate_mode(
        *,
        setting: int,
        mode: ControllerLimitMode,
        setting_name: str,
    ) -> None:
        expected = (
            ControllerLimitMode.ZERO_SETTING_RESPONSE_ESTIMATE
            if setting == 0
            else ControllerLimitMode.CONTROLLER_LIMITED
        )
        if mode is not expected:
            raise ValueError(
                f"Maestro {setting_name} setting {setting} requires mode "
                f"{expected.value!r}"
            )


class ControllerResponseConfig(BaseModel):
    """Versioned response parameters bound to one reviewed calibration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["controller-response-config/v1"]
    model_id: NonEmptyString
    hardware_id: NonEmptyString
    calibration_sha256: Sha256Hex
    model_kind: Literal["piecewise-acceleration-speed-limited"]
    actuators: tuple[ActuatorResponseParameters, ...] = Field(min_length=1)
    provenance: NonEmptyString

    @model_validator(mode="after")
    def validate_actuator_identities(self) -> ControllerResponseConfig:
        names = [item.actuator_name for item in self.actuators]
        if len(names) != len(set(names)):
            raise ValueError("controller response actuator names must be unique")
        return self

    def actuator(self, actuator_name: str) -> ActuatorResponseParameters:
        """Return parameters for an exact semantic actuator identity."""

        for parameters in self.actuators:
            if parameters.actuator_name == actuator_name:
                return parameters
        raise ValueError(f"unknown controller-response actuator: {actuator_name!r}")


class ControllerState(BaseModel):
    """Estimated normalized output state for one semantic actuator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["controller-state/v1"]
    actuator_name: NonEmptyString
    calibration_sha256: Sha256Hex
    position: NormalizedPosition
    velocity: FiniteFloat


class ControllerResponse:
    """Predict monotone response without importing or accessing hardware."""

    def __init__(self, *, config: ControllerResponseConfig) -> None:
        self._config = config

    @property
    def config(self) -> ControllerResponseConfig:
        return self._config

    def predict(
        self,
        state: ControllerState,
        update: TargetUpdate,
        elapsed_s: float,
    ) -> ControllerState:
        """Advance one acceleration/cruise segment, clamped at its target."""

        if not math.isfinite(elapsed_s) or elapsed_s < 0.0:
            raise ValueError("elapsed_s must be finite and non-negative")
        if state.calibration_sha256 != self._config.calibration_sha256:
            raise ValueError("controller state calibration identity mismatch")

        parameters = self._config.actuator(state.actuator_name)
        matching_targets = tuple(
            target
            for target in update.targets
            if target.actuator_name == state.actuator_name
        )
        if not matching_targets:
            raise ValueError(
                f"target update does not contain actuator {state.actuator_name!r}"
            )
        if elapsed_s == 0.0:
            return state
        target_position = matching_targets[0].normalized_position
        distance = target_position - state.position
        if distance == 0.0:
            return state.model_copy(update={"velocity": 0.0})

        direction = math.copysign(1.0, distance)
        remaining = abs(distance)
        # A reported velocity away from the new target is not extrapolated: this
        # abstraction models only the monotone segment toward the active target.
        initial_speed = max(0.0, direction * state.velocity)
        initial_speed = min(initial_speed, parameters.max_velocity_per_s)
        braking_speed = math.sqrt(
            2.0 * parameters.max_acceleration_per_s2 * remaining
        )
        desired_speed = min(parameters.max_velocity_per_s, braking_speed)

        speed_delta = desired_speed - initial_speed
        transition_s = min(
            elapsed_s,
            abs(speed_delta) / parameters.max_acceleration_per_s2,
        )
        acceleration = math.copysign(
            parameters.max_acceleration_per_s2,
            speed_delta,
        )
        transitioned_speed = initial_speed + acceleration * transition_s
        traveled = (
            initial_speed * transition_s
            + 0.5 * acceleration * transition_s**2
            + desired_speed * (elapsed_s - transition_s)
        )

        if traveled >= remaining:
            next_position = target_position
            next_velocity = 0.0
        else:
            next_position = state.position + direction * traveled
            next_velocity = direction * transitioned_speed
        return state.model_copy(
            update={"position": next_position, "velocity": next_velocity}
        )


def load_controller_response_config(
    path: str | Path,
) -> ControllerResponseConfig:
    """Load and validate response parameters without touching hardware."""

    with Path(path).open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise ValueError("controller response config root must be a mapping")
    return ControllerResponseConfig.model_validate(document)
