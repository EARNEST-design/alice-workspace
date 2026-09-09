"""Seeded, cadence-limited procedural motion around reviewed anchors."""

from __future__ import annotations

import copy
import hashlib
import math
from dataclasses import dataclass

import numpy as np

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import MotionProposal, TargetUpdate, TargetUpdateHorizon
from alice.motion.anchors import AnchorPlanner, ProceduralMotionConfig
from alice.motion.intent_filter import FilteredIntent, SupportStatus
from alice.motion.state import ProceduralContinuation, ProceduralEvent


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

        return self._proposal(
            intent,
            state,
            seed,
            horizon_s,
            horizon,
            generated_monotonic_ns=generated_monotonic_ns,
        )

    def _proposal(
        self,
        intent: FilteredIntent,
        state: TargetUpdate,
        seed: int,
        horizon_s: float,
        horizon: TargetUpdateHorizon,
        *,
        generated_monotonic_ns: int,
        continuation_identity: str = "",
    ) -> MotionProposal:
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
        proposal_digest = hashlib.sha256(
            (identity_material + continuation_identity).encode("utf-8")
        ).hexdigest()
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

    def step_continuation(
        self,
        intent: FilteredIntent,
        state: TargetUpdate,
        seed: int,
        horizon_s: float,
        *,
        prefix_duration_s: float,
        generated_monotonic_ns: int,
        continuation: ProceduralContinuation | None,
    ) -> tuple[MotionProposal, ProceduralContinuation]:
        """Render absolute variation, committing its scheduler only to the prefix."""

        if not 0.0 < prefix_duration_s <= horizon_s:
            raise ValueError("prefix duration must be positive and within horizon")
        if continuation is None:
            continuation = self._initial_continuation(seed, generated_monotonic_ns)
        if generated_monotonic_ns < continuation.monotonic_ns:
            raise ValueError("generation time precedes procedural continuation")
        if set(continuation.drift) != set(self._config.drift.actuator_names):
            raise ValueError("procedural drift channels do not match config")
        if set(continuation.applied_variation) != set(
            self._config.semantic_actuator_names
        ):
            raise ValueError("procedural variation channels do not match config")
        supported = intent.support_status not in {
            SupportStatus.FALLBACK,
            SupportStatus.STALE,
        }
        anchor = (
            self._anchor_planner.plan(intent, state, horizon_s)
            if supported
            else self._anchor_planner.plan_neutral(state, horizon_s)
        )
        _, blinks, gazes = self._advance_continuation(
            continuation,
            generated_monotonic_ns + round(horizon_s * 1e9),
        )
        ends_ns = generated_monotonic_ns + round(prefix_duration_s * 1e9)
        boundary, _, _ = self._advance_continuation(continuation, ends_ns)
        applied = dict(continuation.applied_variation)
        updates = []
        for base in anchor.updates:
            if base.offset_s == 0.0:
                updates.append(state)
                continue
            positions = {t.actuator_name: t.normalized_position for t in base.targets}
            variation = dict.fromkeys(self._config.semantic_actuator_names, 0.0)
            if supported:
                at_ns = generated_monotonic_ns + round(base.offset_s * 1e9)
                elapsed_s = (at_ns - continuation.epoch_monotonic_ns) / 1e9
                for name, components in continuation.drift.items():
                    variation[name] += self._drift_value(
                        components,
                        amplitude=self._config.drift.amplitude(name),
                        at_s=elapsed_s,
                    )
                for events, names, sign in (
                    (blinks, self._config.blink.actuator_names, -1.0),
                    (gazes, self._config.gaze.actuator_names, 1.0),
                ):
                    value = sum(
                        self._absolute_envelope(event, at_ns) for event in events
                    )
                    for name in names:
                        variation[name] += sign * value
                remaining = 1.0 - self._smoothstep(
                    base.offset_s / self._config.anchor_transition_s
                )
                for name in positions:
                    # Remove the previous variation carried by the anchor's start.
                    base_position = positions[name] - remaining * applied[name]
                    positions[name] = max(
                        -1.0, min(1.0, base_position + variation[name])
                    )
                    variation[name] = positions[name] - base_position
            updates.append(self._update(base.offset_s, positions))
            if base.offset_s <= prefix_duration_s:
                boundary = boundary.model_copy(update={"applied_variation": variation})
        horizon = TargetUpdateHorizon(
            schema_version="target-update-horizon/v1",
            updates=tuple(updates),
        )
        return self._proposal(
            intent,
            state,
            continuation.seed,
            horizon_s,
            horizon,
            generated_monotonic_ns=generated_monotonic_ns,
            continuation_identity=continuation.model_dump_json(),
        ), ProceduralContinuation.model_validate(boundary.model_dump())

    def _initial_continuation(self, seed: int, at_ns: int) -> ProceduralContinuation:
        rng = np.random.default_rng(seed)
        drift = self._sample_drift(rng)
        blink = self._next_event(rng, at_ns, blink=True)
        gaze = self._next_event(rng, at_ns, blink=False)
        return ProceduralContinuation(
            seed=seed,
            epoch_monotonic_ns=at_ns,
            monotonic_ns=at_ns,
            drift=drift,
            blink=blink,
            gaze=gaze,
            numpy_rng_state=dict(rng.bit_generator.state),
            applied_variation=dict.fromkeys(self._config.semantic_actuator_names, 0.0),
        )

    def _next_event(
        self,
        rng: np.random.Generator,
        after_ns: int,
        *,
        blink: bool,
    ) -> ProceduralEvent:
        if blink:
            timer = self._config.blink
            delay = float(rng.uniform(timer.refractory_s, timer.interval_max_s))
            transition_in, hold, transition_out = (
                timer.close_s,
                timer.hold_s,
                timer.open_s,
            )
            amplitude = timer.amplitude
        else:
            gaze = self._config.gaze
            delay = float(rng.uniform(gaze.refractory_s, gaze.interval_max_s))
            transition_in, hold, transition_out = (
                gaze.transition_s,
                gaze.hold_s,
                gaze.transition_s,
            )
            amplitude = gaze.amplitude * (-1.0 if float(rng.random()) < 0.5 else 1.0)
            amplitude *= float(rng.uniform(0.5, 1.0))
        return ProceduralEvent(
            starts_monotonic_ns=after_ns + round(delay * 1e9),
            transition_in_s=transition_in,
            hold_s=hold,
            transition_out_s=transition_out,
            amplitude=amplitude,
        )

    def _advance_continuation(
        self,
        continuation: ProceduralContinuation,
        until_ns: int,
    ) -> tuple[
        ProceduralContinuation, tuple[ProceduralEvent, ...], tuple[ProceduralEvent, ...]
    ]:
        rng = np.random.default_rng()
        rng.bit_generator.state = copy.deepcopy(continuation.numpy_rng_state)
        blink, gaze = continuation.blink, continuation.gaze
        blinks, gazes = [blink], [gaze]
        # Draw in chronological end-time order, independent of lookahead length.
        while min(blink.ends_monotonic_ns, gaze.ends_monotonic_ns) <= until_ns:
            if blink.ends_monotonic_ns <= gaze.ends_monotonic_ns:
                blink = self._next_event(rng, blink.ends_monotonic_ns, blink=True)
                blinks.append(blink)
            else:
                gaze = self._next_event(rng, gaze.ends_monotonic_ns, blink=False)
                gazes.append(gaze)
        values = continuation.model_dump()
        values.update(
            monotonic_ns=until_ns,
            blink=blink,
            gaze=gaze,
            numpy_rng_state=dict(rng.bit_generator.state),
        )
        return (
            ProceduralContinuation.model_validate(values),
            tuple(blinks),
            tuple(gazes),
        )

    @classmethod
    def _absolute_envelope(cls, event: ProceduralEvent, at_ns: int) -> float:
        return cls._event_envelope(
            _TimedEvent(
                starts_at_s=0.0,
                transition_in_s=event.transition_in_s,
                hold_s=event.hold_s,
                transition_out_s=event.transition_out_s,
                amplitude=event.amplitude,
            ),
            (at_ns - event.starts_monotonic_ns) / 1e9,
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
                self._event_envelope(event, base_update.offset_s) for event in blinks
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
        return (
            amplitude
            * 0.5
            * sum(
                math.sin(2.0 * math.pi * frequency * at_s + phase) - math.sin(phase)
                for frequency, phase in components
            )
            / len(components)
        )

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
