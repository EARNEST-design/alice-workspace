"""Reviewed expression anchors and hardware-independent horizon planning."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.affect import AffectCoordinate
from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.contracts.motion import TargetUpdate, TargetUpdateHorizon
from alice.motion.intent_filter import FilteredIntent, SupportStatus

FinitePositiveFloat = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
UnitIntervalFloat = Annotated[
    float,
    Field(ge=0.0, le=1.0, allow_inf_nan=False),
]


class ExpressionAnchor(BaseModel):
    """One operator-reviewed normalized pose without an implied affect label."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonEmptyString
    targets: tuple[ActuatorTarget, ...] = Field(min_length=1)
    provenance: NonEmptyString

    @model_validator(mode="after")
    def validate_target_names(self) -> ExpressionAnchor:
        names = [target.actuator_name for target in self.targets]
        if len(names) != len(set(names)):
            raise ValueError("expression anchor target names must be unique")
        return self


class AffectAnchorMapping(BaseModel):
    """An independently evidenced continuous coordinate-to-anchor association."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    anchor_name: NonEmptyString
    coordinate: tuple[AffectCoordinate, ...] = Field(min_length=1)
    provenance: NonEmptyString


class DriftAmplitude(BaseModel):
    """Immutable drift amplitude for one semantic actuator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actuator_name: NonEmptyString
    amplitude: UnitIntervalFloat


class DriftConfig(BaseModel):
    """Band-limited natural-variation envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    components: Annotated[int, Field(ge=1)]
    min_frequency_hz: FinitePositiveFloat
    max_frequency_hz: FinitePositiveFloat
    amplitudes: tuple[DriftAmplitude, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_frequency_band(self) -> DriftConfig:
        if self.min_frequency_hz > self.max_frequency_hz:
            raise ValueError("drift frequency minimum must not exceed maximum")
        names = [item.actuator_name for item in self.amplitudes]
        if len(names) != len(set(names)):
            raise ValueError("drift actuator names must be unique")
        return self

    @property
    def actuator_names(self) -> tuple[str, ...]:
        """Return configured channels in stable replay order."""

        return tuple(item.actuator_name for item in self.amplitudes)

    def amplitude(self, actuator_name: str) -> float:
        """Return the configured envelope for an exact semantic identity."""

        for item in self.amplitudes:
            if item.actuator_name == actuator_name:
                return item.amplitude
        raise ValueError(f"unknown drift actuator: {actuator_name!r}")


class BlinkTimerConfig(BaseModel):
    """Configured coupled-eyelid blink timing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actuator_names: tuple[NonEmptyString, NonEmptyString]
    amplitude: UnitIntervalFloat
    close_s: FinitePositiveFloat
    hold_s: FinitePositiveFloat
    open_s: FinitePositiveFloat
    refractory_s: FinitePositiveFloat
    interval_max_s: FinitePositiveFloat

    @model_validator(mode="after")
    def validate_timer(self) -> BlinkTimerConfig:
        if len(set(self.actuator_names)) != 2:
            raise ValueError("blink actuator names must be distinct")
        if self.interval_max_s < self.refractory_s:
            raise ValueError("blink interval maximum must cover its refractory time")
        return self


class GazeTimerConfig(BaseModel):
    """Configured coupled horizontal-gaze timing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actuator_names: tuple[NonEmptyString, NonEmptyString]
    amplitude: UnitIntervalFloat
    transition_s: FinitePositiveFloat
    hold_s: FinitePositiveFloat
    refractory_s: FinitePositiveFloat
    interval_max_s: FinitePositiveFloat

    @model_validator(mode="after")
    def validate_timer(self) -> GazeTimerConfig:
        if len(set(self.actuator_names)) != 2:
            raise ValueError("gaze actuator names must be distinct")
        if self.interval_max_s < self.refractory_s:
            raise ValueError("gaze interval maximum must cover its refractory time")
        return self


class ProceduralMotionConfig(BaseModel):
    """Versioned anchor and procedural baseline configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["procedural-motion-config/v1"]
    model_id: NonEmptyString
    affect_schema_id: NonEmptyString
    affect_dimensions: tuple[NonEmptyString, ...] = Field(min_length=1)
    calibration_sha256: Sha256Hex
    controller_settings_sha256: Sha256Hex
    effective_cadence_hz: FinitePositiveFloat
    anchor_transition_s: FinitePositiveFloat
    semantic_actuator_names: tuple[NonEmptyString, ...] = Field(min_length=1)
    anchors: tuple[ExpressionAnchor, ...] = Field(min_length=1)
    affect_anchor_mappings: tuple[AffectAnchorMapping, ...] = ()
    drift: DriftConfig
    blink: BlinkTimerConfig
    gaze: GazeTimerConfig
    provenance: NonEmptyString

    @model_validator(mode="after")
    def validate_references(self) -> ProceduralMotionConfig:
        if len(self.affect_dimensions) != len(set(self.affect_dimensions)):
            raise ValueError("affect dimensions must be unique")
        if len(self.semantic_actuator_names) != len(
            set(self.semantic_actuator_names)
        ):
            raise ValueError("semantic actuator names must be unique")
        anchor_names = [anchor.name for anchor in self.anchors]
        if len(anchor_names) != len(set(anchor_names)):
            raise ValueError("expression anchor names must be unique")
        if "neutral" not in anchor_names:
            raise ValueError("procedural motion config requires a neutral anchor")

        semantic_names = set(self.semantic_actuator_names)
        for anchor in self.anchors:
            unknown = {
                target.actuator_name for target in anchor.targets
            } - semantic_names
            if unknown:
                raise ValueError(
                    f"anchor contains unknown semantic actuators: {unknown}"
                )
        neutral_names = {
            target.actuator_name for target in self.anchor("neutral").targets
        }
        if neutral_names != semantic_names:
            raise ValueError("neutral anchor must define every semantic actuator")

        known_anchors = set(anchor_names)
        for mapping in self.affect_anchor_mappings:
            if mapping.anchor_name not in known_anchors:
                raise ValueError("affect mapping references an unknown anchor")
            if len(mapping.coordinate) != len(self.affect_dimensions):
                raise ValueError("affect mapping width must match affect dimensions")
        mapped_names = [mapping.anchor_name for mapping in self.affect_anchor_mappings]
        if len(mapped_names) != len(set(mapped_names)):
            raise ValueError("an anchor may have only one affect mapping")

        configured_channels = (
            set(self.drift.actuator_names)
            | set(self.blink.actuator_names)
            | set(self.gaze.actuator_names)
        )
        if not configured_channels <= semantic_names:
            raise ValueError("procedural channels must use semantic actuator names")
        if self.drift.max_frequency_hz >= self.effective_cadence_hz / 2.0:
            raise ValueError("drift frequency must remain below cadence Nyquist limit")
        cadence_period_s = 1.0 / self.effective_cadence_hz
        if min(
            self.blink.close_s,
            self.blink.hold_s,
            self.blink.open_s,
            self.gaze.transition_s,
            self.gaze.hold_s,
        ) < cadence_period_s:
            raise ValueError("event phases must be observable at effective cadence")
        return self

    def anchor(self, name: str) -> ExpressionAnchor:
        """Return an anchor by its stable reviewed name."""

        for anchor in self.anchors:
            if anchor.name == name:
                return anchor
        raise ValueError(f"unknown expression anchor: {name!r}")


class AnchorPlanner:
    """Build cadence-aligned horizons from accepted state to reviewed anchors."""

    def __init__(self, *, config: ProceduralMotionConfig) -> None:
        self._config = config
        self._neutral = self._complete_anchor("neutral")

    @property
    def config(self) -> ProceduralMotionConfig:
        return self._config

    def plan(
        self,
        intent: FilteredIntent,
        state: TargetUpdate,
        horizon_s: float,
    ) -> TargetUpdateHorizon:
        """Plan toward an evidenced anchor interpolation or neutral fallback."""

        self._validate_intent(intent)
        if intent.support_status not in {
            SupportStatus.SUPPORTED,
            SupportStatus.INTERPOLATED,
        }:
            return self.plan_neutral(state, horizon_s)
        target = self._interpolated_target(intent)
        return self._plan_to(target, state, horizon_s)

    def plan_neutral(
        self,
        state: TargetUpdate,
        horizon_s: float,
    ) -> TargetUpdateHorizon:
        """Return smoothly to the reviewed neutral pose."""

        return self._plan_to(self._neutral, state, horizon_s)

    def _plan_to(
        self,
        target: dict[str, float],
        state: TargetUpdate,
        horizon_s: float,
    ) -> TargetUpdateHorizon:
        offsets = self.cadence_offsets(horizon_s)
        start = self._state_positions(state)
        updates = []
        for offset_s in offsets:
            progress = min(1.0, offset_s / self._config.anchor_transition_s)
            blend = progress * progress * (3.0 - 2.0 * progress)
            positions = {
                name: start[name] + blend * (target[name] - start[name])
                for name in self._config.semantic_actuator_names
            }
            updates.append(self._update(offset_s, positions))
        return TargetUpdateHorizon(
            schema_version="target-update-horizon/v1",
            updates=tuple(updates),
        )

    def cadence_offsets(self, horizon_s: float) -> tuple[float, ...]:
        """Return horizon-relative offsets at the configured effective cadence."""

        if not math.isfinite(horizon_s) or horizon_s <= 0.0:
            raise ValueError("horizon_s must be finite and positive")
        count = math.floor(
            horizon_s * self._config.effective_cadence_hz + 1e-12
        )
        return tuple(
            index / self._config.effective_cadence_hz
            for index in range(count + 1)
        )

    def _interpolated_target(self, intent: FilteredIntent) -> dict[str, float]:
        mappings = self._config.affect_anchor_mappings
        if not mappings:
            return self._neutral

        distances = tuple(
            math.dist(intent.vector, mapping.coordinate) for mapping in mappings
        )
        for mapping, distance in zip(mappings, distances, strict=True):
            if distance == 0.0:
                return self._scaled_anchor(mapping.anchor_name, intent.intensity)

        inverse_distances = tuple(1.0 / distance for distance in distances)
        total_weight = sum(inverse_distances)
        weighted = dict(self._neutral)
        for name in self._config.semantic_actuator_names:
            weighted[name] = sum(
                weight * self._complete_anchor(mapping.anchor_name)[name]
                for mapping, weight in zip(
                    mappings,
                    inverse_distances,
                    strict=True,
                )
            ) / total_weight
        return {
            name: self._neutral[name]
            + intent.intensity * (weighted[name] - self._neutral[name])
            for name in self._config.semantic_actuator_names
        }

    def _scaled_anchor(self, name: str, intensity: float) -> dict[str, float]:
        anchor = self._complete_anchor(name)
        return {
            actuator_name: self._neutral[actuator_name]
            + intensity * (anchor[actuator_name] - self._neutral[actuator_name])
            for actuator_name in self._config.semantic_actuator_names
        }

    def _complete_anchor(self, name: str) -> dict[str, float]:
        positions = (
            {}
            if name == "neutral"
            else {
                target.actuator_name: target.normalized_position
                for target in self._config.anchor("neutral").targets
            }
        )
        positions.update(
            {
                target.actuator_name: target.normalized_position
                for target in self._config.anchor(name).targets
            }
        )
        return positions

    def _state_positions(self, state: TargetUpdate) -> dict[str, float]:
        positions = {
            target.actuator_name: target.normalized_position
            for target in state.targets
        }
        expected = set(self._config.semantic_actuator_names)
        if set(positions) != expected:
            raise ValueError("accepted state must define every semantic actuator")
        return positions

    def _validate_intent(self, intent: FilteredIntent) -> None:
        if intent.affect_schema_id != self._config.affect_schema_id:
            raise ValueError("filtered intent affect schema identity mismatch")
        if len(intent.vector) != len(self._config.affect_dimensions):
            raise ValueError("filtered intent vector width mismatch")

    def _update(
        self,
        offset_s: float,
        positions: dict[str, float],
    ) -> TargetUpdate:
        return TargetUpdate(
            offset_s=offset_s,
            targets=tuple(
                ActuatorTarget(
                    actuator_name=name,
                    normalized_position=max(-1.0, min(1.0, positions[name])),
                )
                for name in self._config.semantic_actuator_names
            ),
        )


def load_procedural_motion_config(
    path: str | Path,
) -> ProceduralMotionConfig:
    """Load and validate the baseline configuration without hardware access."""

    with Path(path).open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise ValueError("procedural motion config root must be a mapping")
    return ProceduralMotionConfig.model_validate(document)
