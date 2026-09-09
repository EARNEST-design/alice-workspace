import pytest

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate, TargetUpdateHorizon
from alice.contracts.speech import SpeechSyncConfig
from alice.speech.composer import compose_frame, expression_at_sample
from alice.speech.timeline import SpeechFrame


def target(offset, **positions):
    return TargetUpdate(
        offset_s=offset,
        targets=tuple(
            ActuatorTarget(actuator_name=k, normalized_position=v)
            for k, v in positions.items()
        ),
    )


def frame(aperture, weight=1):
    return SpeechFrame(
        sample_index=100,
        mouth_aperture=aperture,
        speech_weight=weight,
        vector=(0, 0, 0),
        intensity=0,
    )


def test_speech_owns_jaw_preserves_smile_and_releases():
    expression = target(
        0, mouth_open=1, left_mouth_corner=0.7, right_mouth_corner=-0.7, head_tilt=0.2
    )
    closed = compose_frame(expression, frame(0), SpeechSyncConfig())
    positions = {t.actuator_name: t.normalized_position for t in closed.targets}
    assert positions == {
        "mouth_open": -1,
        "left_mouth_corner": 0.7,
        "right_mouth_corner": -0.7,
        "head_tilt": 0.2,
    }
    released = compose_frame(expression, frame(0, 0), SpeechSyncConfig())
    assert released.targets == expression.targets
    opened = compose_frame(expression, frame(1), SpeechSyncConfig())
    assert 0 < opened.targets[0].normalized_position <= 1


def test_sparse_expression_targets_accumulate_on_audio_clock():
    horizon = TargetUpdateHorizon(
        schema_version="target-update-horizon/v1",
        updates=(
            target(0, mouth_open=0, left_mouth_corner=0.2),
            target(0.5, head_tilt=0.3),
            target(0.7, left_mouth_corner=0.6),
        ),
    )
    found = expression_at_sample(horizon, sample_index=600, sample_rate=1000)
    assert {t.actuator_name: t.normalized_position for t in found.targets} == {
        "mouth_open": 0,
        "left_mouth_corner": 0.2,
        "head_tilt": 0.3,
    }
    with pytest.raises(ValueError):
        expression_at_sample(horizon, sample_index=0, sample_rate=0)
