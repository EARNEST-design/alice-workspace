"""Speech overlays semantic expression proposals before downstream validation."""

from __future__ import annotations

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate, TargetUpdateHorizon
from alice.contracts.speech import SpeechSyncConfig
from alice.speech.timeline import SpeechFrame


def expression_at_sample(
    horizon: TargetUpdateHorizon | None,
    *,
    sample_index: int,
    sample_rate: int,
) -> TargetUpdate:
    """Accumulate sparse relative-time expression targets on the audio clock.

    The source generator owns interpolation. Never interpolate sparse targets
    here as if they represented measured positions.
    """
    if sample_index < 0 or sample_rate <= 0:
        raise ValueError("invalid audio sample position or sample rate")
    positions = {"mouth_open": 0.0}
    offset = sample_index / sample_rate
    if horizon is not None:
        for update in horizon.updates:
            if update.offset_s > offset:
                break
            positions.update(
                {t.actuator_name: t.normalized_position for t in update.targets}
            )
    return TargetUpdate(
        offset_s=offset,
        targets=tuple(
            ActuatorTarget(actuator_name=name, normalized_position=value)
            for name, value in positions.items()
        ),
    )


def compose_frame(
    expression: TargetUpdate,
    speech: SpeechFrame,
    config: SpeechSyncConfig,
) -> TargetUpdate:
    """Preserve expression channels; gate its jaw contribution by aperture."""
    positions = {t.actuator_name: t.normalized_position for t in expression.targets}
    baseline = positions.get("mouth_open", 0.0)
    jaw = (
        config.closed_position
        + speech.mouth_aperture * (config.open_position - config.closed_position)
        + config.expression_jaw_weight * speech.mouth_aperture * baseline
    )
    jaw = max(-1.0, min(1.0, jaw))
    positions["mouth_open"] = (
        baseline * (1 - speech.speech_weight) + jaw * speech.speech_weight
    )
    return TargetUpdate(
        offset_s=expression.offset_s,
        targets=tuple(
            ActuatorTarget(actuator_name=name, normalized_position=value)
            for name, value in positions.items()
        ),
    )
