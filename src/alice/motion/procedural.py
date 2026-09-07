"""Seeded, cadence-limited procedural motion around reviewed anchors."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import numpy as np

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import MotionProposal, TargetUpdate, TargetUpdateHorizon
from alice.motion.anchors import AnchorPlanner, ProceduralMotionConfig
from alice.motion.intent_filter import FilteredIntent, SupportStatus


@dataclass(frozen=True)
class _TimedEvent:
    starts_at_s: float
    transition_in_s: float
    hold_s: float
    transition_out_s: float
    amplitude: float

    @property
    def ends_at_s(self) -> float:
        return (
            self.starts_at_s
            + self.transition_in_s
            + self.hold_s
            + self.transition_out_s
        )


class ProceduralMotionGenerator:
    """Add deterministic slow drift and inspectable eye events to anchor plans."""

    def __init__(
        self,
        *,
        config: ProceduralMotionConfig,
        anchor_planner: AnchorPlanner,
    ) -> None:
        if anchor_planner.config != config:
            raise ValueError("anchor planner and procedural config must match")
        self._config = config
        self._anchor_planner = anchor_planner
        self._model_sha256 = hashlib.sha256(
            config.model_dump_json().encode("utf-8")
        ).hexdigest()

    @property
    def config(self) -> ProceduralMotionConfig:
        return self._config

    def step(
        self,
        intent: FilteredIntent,
        state: TargetUpdate,
        seed: int,
        horizon_s: float,
        *,
        generated_monotonic_ns: int,
    ) -> MotionProposal:
        """Generate a proposal at an explicit time, independent of intent age."""

        if seed < 0:
            raise ValueError("seed must be non-negative")
        if intent.support_status in {
            SupportStatus.FALLBACK,
            SupportStatus.STALE,
        }:
            horizon = self._anchor_planner.plan_neutral(state, horizon_s)
        else:
            anchor_horizon = self._anchor_planner.plan(intent, state, horizon_s)
            horizon = self._add_variation(
                anchor_horizon,
                seed=seed,
                horizon_s=horizon_s,
            )

        validity_s = max(horizon_s, horizon.updates[-1].offset_s)
        expires_ns = generated_monotonic_ns + max(
            1,
            math.floor(validity_s * 1_000_000_000) + 1,
        )
        identity_material = "|".join(
            (
                self._model_sha256,
                intent.model_dump_json(),
                state.model_dump_json(),
                str(seed),
                repr(horizon_s),
                str(generated_monotonic_ns),
            )
        )
        proposal_digest = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()
        return MotionProposal(
            schema_version="motion-proposal/v1",
            proposal_id=f"procedural-{proposal_digest[:24]}",
            run_id=f"procedural-{intent.source_id}",
            generated_monotonic_ns=generated_monotonic_ns,
            expires_monotonic_ns=expires_ns,
            seed=seed,
            model_id=self._config.model_id,
            model_sha256=self._model_sha256,
            calibration_sha256=self._config.calibration_sha256,
            controller_settings_sha256=self._config.controller_settings_sha256,
            horizon=horizon,
            support_status=intent.support_status.value,
        )

    def _add_variation(
        self,
        anchor_horizon: TargetUpdateHorizon,
        *,
        seed: int,
        horizon_s: float,
    ) -> TargetUpdateHorizon:
        rng = np.random.default_rng(seed)
        drift = self._sample_drift(rng)
        blinks = self._sample_blinks(rng, horizon_s)
        gazes = self._sample_gazes(rng, horizon_s)
        updates = []
        for base_update in anchor_horizon.updates:
            positions = {
                target.actuator_name: target.normalized_position
                for target in base_update.targets
            }
            for name, components in drift.items():
                positions[name] += self._drift_value(
                    components,
                    amplitude=self._config.drift.amplitude(name),
                    at_s=base_update.offset_s,
                )

            blink_value = sum(
                self._event_envelope(event, base_update.offset_s)
                for event in blinks
            )
            for name in self._config.blink.actuator_names:
                positions[name] -= blink_value

            gaze_value = sum(
                self._signed_event_envelope(event, base_update.offset_s)
                for event in gazes
            )
            for name in self._config.gaze.actuator_names:
                positions[name] += gaze_value

            updates.append(self._update(base_update.offset_s, positions))
        return TargetUpdateHorizon(
            schema_version="target-update-horizon/v1",
            updates=tuple(updates),
        )

    def _sample_drift(
        self,
        rng: np.random.Generator,
    ) -> dict[str, tuple[tuple[float, float], ...]]:
        return {
            name: tuple(
                (
                    float(
                        rng.uniform(
                            self._config.drift.min_frequency_hz,
                            self._config.drift.max_frequency_hz,
                        )
                    ),
                    float(rng.uniform(0.0, 2.0 * math.pi)),
                )
                for _ in range(self._config.drift.components)
            )
            for name in self._config.drift.actuator_names
        }

    def _sample_blinks(
        self,
        rng: np.random.Generator,
        horizon_s: float,
    ) -> tuple[_TimedEvent, ...]:
        timer = self._config.blink
        return self._sample_events(
            rng,
            horizon_s=horizon_s,
            transition_in_s=timer.close_s,
            hold_s=timer.hold_s,
            transition_out_s=timer.open_s,
            amplitude=timer.amplitude,
            refractory_s=timer.refractory_s,
            interval_max_s=timer.interval_max_s,
            signed=False,
        )

    def _sample_gazes(
        self,
        rng: np.random.Generator,
        horizon_s: float,
    ) -> tuple[_TimedEvent, ...]:
        timer = self._config.gaze
        return self._sample_events(
            rng,
            horizon_s=horizon_s,
            transition_in_s=timer.transition_s,
            hold_s=timer.hold_s,
            transition_out_s=timer.transition_s,
            amplitude=timer.amplitude,
            refractory_s=timer.refractory_s,
            interval_max_s=timer.interval_max_s,
            signed=True,
        )

    @staticmethod
    def _sample_events(
        rng: np.random.Generator,
        *,
        horizon_s: float,
        transition_in_s: float,
        hold_s: float,
        transition_out_s: float,
        amplitude: float,
        refractory_s: float,
        interval_max_s: float,
        signed: bool,
    ) -> tuple[_TimedEvent, ...]:
        events = []
        starts_at_s = float(rng.uniform(refractory_s, interval_max_s))
        while starts_at_s <= horizon_s:
            signed_amplitude = amplitude
            if signed:
                signed_amplitude *= -1.0 if float(rng.random()) < 0.5 else 1.0
                signed_amplitude *= float(rng.uniform(0.5, 1.0))
            event = _TimedEvent(
                starts_at_s=starts_at_s,
                transition_in_s=transition_in_s,
                hold_s=hold_s,
                transition_out_s=transition_out_s,
                amplitude=signed_amplitude,
            )
            events.append(event)
            starts_at_s = event.ends_at_s + float(
                rng.uniform(refractory_s, interval_max_s)
            )
        return tuple(events)

    @staticmethod
    def _drift_value(
        components: tuple[tuple[float, float], ...],
        *,
        amplitude: float,
        at_s: float,
    ) -> float:
        return amplitude * 0.5 * sum(
            math.sin(2.0 * math.pi * frequency * at_s + phase)
            - math.sin(phase)
            for frequency, phase in components
        ) / len(components)

    @classmethod
    def _event_envelope(cls, event: _TimedEvent, at_s: float) -> float:
        elapsed_s = at_s - event.starts_at_s
        if elapsed_s < 0.0 or at_s > event.ends_at_s:
            return 0.0
        if elapsed_s < event.transition_in_s:
            return event.amplitude * cls._smoothstep(elapsed_s / event.transition_in_s)
        elapsed_s -= event.transition_in_s
        if elapsed_s <= event.hold_s:
            return event.amplitude
        elapsed_s -= event.hold_s
        return event.amplitude * (
            1.0 - cls._smoothstep(elapsed_s / event.transition_out_s)
        )

    @classmethod
    def _signed_event_envelope(cls, event: _TimedEvent, at_s: float) -> float:
        return math.copysign(
            cls._event_envelope(
                _TimedEvent(
                    starts_at_s=event.starts_at_s,
                    transition_in_s=event.transition_in_s,
                    hold_s=event.hold_s,
                    transition_out_s=event.transition_out_s,
                    amplitude=abs(event.amplitude),
                ),
                at_s,
            ),
            event.amplitude,
        )

    @staticmethod
    def _smoothstep(value: float) -> float:
        clamped = max(0.0, min(1.0, value))
        return clamped * clamped * (3.0 - 2.0 * clamped)

    def _update(self, offset_s: float, positions: dict[str, float]) -> TargetUpdate:
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
