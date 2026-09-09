from datetime import datetime, timezone

import numpy as np
import pytest
from pydantic import ValidationError

from alice.contracts.speech import SpeechPlan, SpeechSyncConfig
from alice.speech.timeline import AudioClip, prepare_speech


def plan(**changes):
    data = {
        "schema_version": "speech-plan/v1",
        "utterance_id": "test",
        "source_id": "test-author",
        "seed": 7,
        "voice": "alba",
        "segments": [
            {
                "text": "Hello.",
                "pause_after_s": 0.2,
                "cues": [
                    {"progress": 0, "vector": [0, 0, 0], "intensity": 0.5},
                    {"progress": 1, "vector": [1, 0, -1], "intensity": 1},
                ],
            }
        ],
    }
    data.update(changes)
    return SpeechPlan.model_validate(data)


class SyntheticVoice:
    identity = {"backend": "synthetic-test", "model": "tone-v1"}

    def synthesize(self, text, *, voice, seed):
        assert voice == "alba"
        # Different durations make text-length timing visibly incorrect.
        n = 1000 if text == "Hello." else 2000
        return AudioClip(np.full(n, 0.2, dtype=np.float32), 1000)


@pytest.mark.parametrize(
    "changes",
    [
        {"segments": []},
        {"voice": "/private/person.wav"},
        {"affect_schema_id": "other"},
        {"seed": -1},
        {"segments": [{"text": "   ", "cues": []}]},
        {
            "segments": [
                {
                    "text": "ok",
                    "cues": [{"progress": 0.2, "vector": [0, 0, 0], "intensity": 1}],
                }
            ]
        },
        {
            "segments": [
                {
                    "text": "ok",
                    "cues": [
                        {"progress": 0, "vector": [float("nan"), 0, 0], "intensity": 1}
                    ],
                }
            ]
        },
        {
            "segments": [
                {
                    "text": "ok",
                    "cues": [{"progress": 0, "vector": [0, 0], "intensity": 1}],
                }
            ]
        },
        {
            "segments": [
                {
                    "text": "ok",
                    "cues": [
                        {"progress": 0, "vector": [0, 0, 0], "intensity": 1},
                        {"progress": 0, "vector": [1, 0, 0], "intensity": 1},
                    ],
                }
            ]
        },
    ],
)
def test_reject_invalid_llm_plan(changes):
    with pytest.raises(ValidationError):
        plan(**changes)


def test_timeline_uses_actual_samples_and_explicit_pauses():
    value = plan()
    second = value.segments[0].model_dump()
    second.update(text="A second clause.", pause_after_s=0)
    prepared = prepare_speech(
        plan(segments=[value.segments[0].model_dump(), second]), SyntheticVoice()
    )
    assert [(s.start_sample, s.end_sample) for s in prepared.spans] == [
        (0, 1000),
        (1200, 3200),
    ]
    assert np.all(prepared.audio.pcm[1000:1200] == 0)
    assert prepared.at_sample(500).vector == pytest.approx((0.5, 0, -0.5))
    assert prepared.at_sample(2200).vector == pytest.approx((0.5, 0, -0.5))
    assert prepared.at_sample(500).mouth_aperture > 0.9
    assert prepared.at_sample(1180).mouth_aperture < 0.1
    assert prepared.at_sample(len(prepared.audio.pcm)).speech_weight == 0
    assert prepared.at_sample(len(prepared.audio.pcm)).mouth_aperture == 0


def test_silence_stays_closed_and_invalid_pcm_is_rejected():
    class Silent(SyntheticVoice):
        def synthesize(self, *args, **kwargs):
            return AudioClip(np.zeros(400, dtype=np.float32), 1000)

    prepared = prepare_speech(plan(), Silent())
    assert all(frame.mouth_aperture == 0 for frame in prepared.frames)
    for pcm, rate in [
        (np.array([np.nan]), 1000),
        (np.ones((2, 2)), 1000),
        (np.ones(2), 0),
        (np.array([]), 1000),
        (np.array([2.0]), 1000),
    ]:
        with pytest.raises(ValueError):
            AudioClip(pcm, rate)


def test_rate_mismatch_and_resource_limit_fail():
    class ChangingRate(SyntheticVoice):
        def synthesize(self, text, **kwargs):
            return AudioClip(
                np.ones(200, dtype=np.float32) * 0.1, 1000 if text == "Hello." else 2000
            )

    segments = [
        plan().segments[0].model_dump(),
        {
            "text": "next",
            "cues": [{"progress": 0, "vector": [0, 0, 0], "intensity": 0}],
        },
    ]
    with pytest.raises(ValueError, match="sample rate"):
        prepare_speech(plan(segments=segments), ChangingRate())
    with pytest.raises(ValueError, match="duration"):
        prepare_speech(plan(), SyntheticVoice(), SpeechSyncConfig(max_duration_s=0.5))


def test_affect_intent_uses_playback_time_and_expires():
    prepared = prepare_speech(plan(), SyntheticVoice())
    intent = prepared.affect_intent(
        500, now_ns=10_000_000_000, captured_at=datetime.now(timezone.utc)
    )
    assert intent.vector == pytest.approx((0.5, 0, -0.5))
    assert intent.received_monotonic_ns == 10_000_000_000
    assert intent.expires_monotonic_ns == 10_100_000_000
    assert (
        prepared.affect_intent(
            len(prepared.audio.pcm), now_ns=0, captured_at=datetime.now(timezone.utc)
        )
        is None
    )
    with pytest.raises(ValueError):
        prepared.at_sample(-1)
