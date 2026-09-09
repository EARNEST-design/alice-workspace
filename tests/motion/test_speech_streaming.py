"""Speech over the completed streaming baseline, with explicit support limits."""

import numpy as np
import pytest
import yaml
from speech_replay_helpers import replay_speech

from alice.contracts.speech import SpeechPlan
from alice.speech.composer import compose_frame, expression_at_sample
from alice.speech.timeline import AudioClip, prepare_speech


class PulsedVoice:
    identity = {"backend": "synthetic-pulses"}

    def synthesize(self, text, *, voice, seed):
        return AudioClip(np.tile(np.r_[np.full(500, 0.2), np.zeros(500)], 6), 1000)


@pytest.mark.parametrize("mode", ["speech-filtered", "neutral-priors-only"])
def test_speech_shares_streaming_clock_and_preserves_expression_ownership(mode):
    plan = SpeechPlan.model_validate(
        {
            "schema_version": "speech-plan/v1",
            "utterance_id": "speech-streaming-qualification",
            "source_id": "synthetic-qualification",
            "seed": 7,
            "segments": [
                {
                    "text": "Synthetic clock and ownership test.",
                    "cues": [
                        {"progress": 0, "vector": [0.6, 0.2, 0.1], "intensity": 0.7},
                        {"progress": 1, "vector": [-0.2, -0.1, 0], "intensity": 0.3},
                    ],
                }
            ],
        }
    )
    prepared = prepare_speech(plan, PulsedVoice())
    horizon, report = replay_speech(prepared, mode=mode)
    assert replay_speech(prepared, mode=mode) == (horizon, report)
    assert report["exact_restart_equal"]
    assert horizon.updates[0].offset_s == 0
    assert horizon.updates[-1].offset_s >= len(prepared.audio.pcm) / 1000
    if mode == "speech-filtered":
        assert set(report["intent_status_counts"]) == {"fallback"}
        assert report["requested_affect_changed"]
        assert not report["accepted_affect_changed"]
    else:
        assert report["motion_event_count"] > 0
        assert not report["requested_affect_drives_motion"]

    jaws = []
    for frame in prepared.frames:
        base = expression_at_sample(
            horizon, sample_index=frame.sample_index, sample_rate=1000
        )
        mixed = compose_frame(base, frame, prepared.config)
        before = {t.actuator_name: t.normalized_position for t in base.targets}
        after = {t.actuator_name: t.normalized_position for t in mixed.targets}
        assert len(after) == 11
        assert mixed.offset_s == frame.sample_index / 1000
        assert all(after[k] == v for k, v in before.items() if k != "mouth_open")
        assert all(-1 <= v <= 1 for v in after.values())
        jaws.append(after["mouth_open"])
    assert max(jaws) - min(jaws) > 1
    assert jaws[-1] == before["mouth_open"]  # normal end releases speech ownership


def test_qualification_refuses_to_relabel_new_support_as_empty(tmp_path, monkeypatch):
    import speech_replay_helpers as helper

    source = helper.CONFIG.parent / "affect"
    affect = tmp_path / "affect"
    affect.mkdir()
    (affect / "affect-vector-v1.yaml").write_bytes(
        (source / "affect-vector-v1.yaml").read_bytes()
    )
    config = yaml.safe_load((source / "intent-filter-v1.yaml").read_text())
    config["retained_training_coordinates"] = [[0, 0, 0]]
    (affect / "intent-filter-v1.yaml").write_text(yaml.safe_dump(config))
    monkeypatch.setattr(helper, "CONFIG", tmp_path / "models")
    plan = SpeechPlan.model_validate_json(
        (source.parent / "speech/alice-sync-test.json").read_text()
    )
    with pytest.raises(ValueError, match="empty affect support set"):
        replay_speech(prepare_speech(plan, PulsedVoice()), mode="speech-filtered")
