"""Deterministic, hardware-independent Maestro/servo response estimation."""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.contracts.motion import TargetUpdate

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
FinitePositiveFloat = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
NormalizedPosition = Annotated[
    float,
    Field(ge=-1.0, le=1.0, allow_inf_nan=False),
]
_KINEMATIC_TOLERANCE = 1e-12


class InfeasibleControllerTargetError(ValueError):
    """A valid moving state cannot realize the requested target safely."""


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

    @property
    def controller_settings_sha256(self) -> str:
        """Hash the actual ordered firmware settings embedded in this config."""

        settings = [
            {
                "actuator_name": item.actuator_name,
                "firmware_speed_setting": item.firmware_speed_setting,
                "firmware_acceleration_setting": item.firmware_acceleration_setting,
            }
            for item in self.actuators
        ]
        encoded = json.dumps(settings, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @property
    def response_sha256(self) -> str:
        """Hash the canonical complete response configuration."""

        encoded = json.dumps(
            self.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


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

    def is_feasible(self, state: ControllerState, update: TargetUpdate) -> bool:
        """Return whether a target is feasible without advancing controller state."""

        try:
            self.predict(state, update, elapsed_s=0.0)
        except InfeasibleControllerTargetError:
            return False
        return True

    def predict(
        self,
        state: ControllerState,
        update: TargetUpdate,
        elapsed_s: float,
    ) -> ControllerState:
        """Advance an exact accelerate/cruise/brake trajectory toward target."""

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
        target_position = matching_targets[0].normalized_position
        if abs(state.velocity) > parameters.max_velocity_per_s:
            raise ValueError(
                f"state velocity exceeds configured limit for {state.actuator_name!r}"
            )
        distance = target_position - state.position
        if distance == 0.0:
            if state.velocity != 0.0:
                raise InfeasibleControllerTargetError(
                    "state velocity exceeds available stopping distance"
                )
            return state

        direction = math.copysign(1.0, distance)
        remaining = abs(distance)
        initial_speed = direction * state.velocity
        acceleration_limit = parameters.max_acceleration_per_s2
        if initial_speed < 0.0:
            outward_excursion = initial_speed**2 / (2.0 * acceleration_limit)
            turnaround_position = state.position - direction * outward_excursion
            if not -1.0 <= turnaround_position <= 1.0:
                raise InfeasibleControllerTargetError(
                    "reversal braking would cross a normalized endpoint"
                )
        stopping_distance = (
            initial_speed**2 / (2.0 * acceleration_limit)
            if initial_speed > 0.0
            else 0.0
        )
        if stopping_distance > remaining:
            raise InfeasibleControllerTargetError(
                "state velocity exceeds available stopping distance"
            )
        if elapsed_s == 0.0:
            return state
        trajectory_distance = max(remaining, stopping_distance)

        segments = self._trajectory_segments(
            distance=trajectory_distance,
            initial_speed=initial_speed,
            max_speed=parameters.max_velocity_per_s,
            acceleration=acceleration_limit,
        )
        arrival_s = sum(duration for duration, _ in segments)
        if elapsed_s >= arrival_s:
            return self._updated_state(
                state,
                position=target_position,
                velocity=0.0,
                target_position=target_position,
                direction=direction,
                parameters=parameters,
            )

        traveled = 0.0
        speed = initial_speed
        time_left = elapsed_s
        for duration, acceleration in segments:
            segment_s = min(time_left, duration)
            traveled += speed * segment_s + 0.5 * acceleration * segment_s**2
            speed += acceleration * segment_s
            time_left -= segment_s
            if time_left <= 0.0:
                break

        next_position = state.position + direction * traveled
        next_velocity = direction * speed
        return self._updated_state(
            state,
            position=next_position,
            velocity=next_velocity,
            target_position=target_position,
            direction=direction,
            parameters=parameters,
        )

    @staticmethod
    def _updated_state(
        state: ControllerState,
        *,
        position: float,
        velocity: float,
        target_position: float,
        direction: float,
        parameters: ActuatorResponseParameters,
    ) -> ControllerState:
        """Build a strict successor after normalizing solver-scale boundary noise."""

        position, velocity = ControllerResponse._normalize_generated_boundaries(
            position=position,
            velocity=velocity,
            target_position=target_position,
            direction=direction,
            max_velocity=parameters.max_velocity_per_s,
            acceleration=parameters.max_acceleration_per_s2,
        )

        return ControllerState(
            schema_version=state.schema_version,
            actuator_name=state.actuator_name,
            calibration_sha256=state.calibration_sha256,
            position=position,
            velocity=velocity,
        )

    @staticmethod
    def _normalize_generated_boundaries(
        *,
        position: float,
        velocity: float,
        target_position: float,
        direction: float,
        max_velocity: float,
        acceleration: float,
    ) -> tuple[float, float]:
        """Canonicalize only tolerance-sized errors created by this solver."""

        if position < -1.0 or position > 1.0:
            endpoint_error = max(-1.0 - position, position - 1.0)
            ControllerResponse._require_internal_tolerance(
                endpoint_error,
                boundary="normalized position",
            )
            position = max(-1.0, min(1.0, position))

        directed_remaining = direction * (target_position - position)
        if directed_remaining < 0.0:
            target_speed = max(0.0, direction * velocity)
            stopping_distance = target_speed**2 / (2.0 * acceleration)
            ControllerResponse._require_internal_tolerance(
                max(-directed_remaining, stopping_distance),
                boundary="target overshoot",
            )
            return target_position, 0.0

        if abs(velocity) > max_velocity:
            ControllerResponse._require_internal_tolerance(
                abs(velocity) - max_velocity,
                boundary="velocity limit",
            )
            velocity = math.copysign(max_velocity, velocity)

        directed_speed = direction * velocity
        if directed_speed > 0.0:
            stopping_distance = directed_speed**2 / (2.0 * acceleration)
            if stopping_distance > directed_remaining:
                ControllerResponse._require_internal_tolerance(
                    stopping_distance - directed_remaining,
                    boundary="stopping distance",
                )
                safe_speed = ControllerResponse._safe_speed_for_distance(
                    distance=directed_remaining,
                    acceleration=acceleration,
                )
                velocity = direction * safe_speed
        elif directed_speed < 0.0:
            outward_distance = 1.0 + direction * position
            stopping_distance = directed_speed**2 / (2.0 * acceleration)
            if stopping_distance > outward_distance:
                ControllerResponse._require_internal_tolerance(
                    stopping_distance - outward_distance,
                    boundary="reversal endpoint",
                )
                safe_speed = ControllerResponse._safe_speed_for_distance(
                    distance=outward_distance,
                    acceleration=acceleration,
                )
                velocity = -direction * safe_speed
        return position, velocity

    @staticmethod
    def _safe_speed_for_distance(*, distance: float, acceleration: float) -> float:
        """Return the largest representable speed with an exact safe stop."""

        speed = math.sqrt(2.0 * acceleration * max(0.0, distance))
        while speed**2 / (2.0 * acceleration) > distance:
            speed = math.nextafter(speed, 0.0)
        return speed

    @staticmethod
    def _require_internal_tolerance(error: float, *, boundary: str) -> None:
        if error > _KINEMATIC_TOLERANCE:
            raise RuntimeError(f"controller solver crossed {boundary} by {error!r}")

    @staticmethod
    def _trajectory_segments(
        *,
        distance: float,
        initial_speed: float,
        max_speed: float,
        acceleration: float,
    ) -> tuple[tuple[float, float], ...]:
        """Return time/acceleration segments ending at rest on the target."""

        stopping_distance = (
            initial_speed**2 / (2.0 * acceleration) if initial_speed > 0.0 else 0.0
        )
        if stopping_distance == distance:
            return ((initial_speed / acceleration, -acceleration),)

        unconstrained_peak = math.sqrt(acceleration * distance + 0.5 * initial_speed**2)
        peak_speed = min(max_speed, unconstrained_peak)
        acceleration_s = (peak_speed - initial_speed) / acceleration
        acceleration_distance = (peak_speed**2 - initial_speed**2) / (
            2.0 * acceleration
        )
        braking_s = peak_speed / acceleration
        braking_distance = peak_speed**2 / (2.0 * acceleration)
        cruise_distance = max(
            0.0,
            distance - acceleration_distance - braking_distance,
        )
        cruise_s = cruise_distance / peak_speed
        return (
            (acceleration_s, acceleration),
            (cruise_s, 0.0),
            (braking_s, -acceleration),
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
