import json

import pytest
from test_timeline import SyntheticVoice, plan

from alice.speech.artifacts import write_artifacts
from alice.speech.timeline import prepare_speech


def test_load_verified_recording_and_reject_modified_inputs(tmp_path):
    from alice.speech.recording import load_recording

    original = prepare_speech(plan(), SyntheticVoice())
    path = tmp_path / "speech"
    write_artifacts(original, path, synthesis_seconds=1)
    loaded = load_recording(path)
    assert loaded.frames == original.frames
    assert loaded.spans == original.spans
    assert loaded.audio.sample_rate == original.audio.sample_rate
    assert len(loaded.audio.pcm) == len(original.audio.pcm)
    (path / "plan.json").write_text(json.dumps({"voice": "azelma"}))
    with pytest.raises(ValueError, match="checksum"):
        load_recording(path)


@pytest.mark.parametrize("corruption", ["clock", "pcm"])
def test_rehashed_malformed_clock_or_stereo_pcm_is_rejected(tmp_path, corruption):
    import hashlib
    import wave

    from alice.speech.recording import load_recording

    tmp_path = tmp_path / "speech"
    write_artifacts(
        prepare_speech(plan(), SyntheticVoice()), tmp_path, synthesis_seconds=1
    )
    name = "timeline.json" if corruption == "clock" else "speech.wav"
    if corruption == "clock":
        timeline = json.loads((tmp_path / name).read_text())
        timeline["frames"][1]["sample_index"] = 0
        (tmp_path / name).write_text(json.dumps(timeline))
    else:
        with wave.open(str(tmp_path / name), "wb") as wav:
            wav.setparams((2, 2, 24000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0" * 400)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["artifacts"][name] = hashlib.sha256(
        (tmp_path / name).read_bytes()
    ).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="PCM16|ordered"):
        load_recording(tmp_path)
