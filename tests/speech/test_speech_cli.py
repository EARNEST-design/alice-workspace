import hashlib
import json
import wave

import numpy as np
import pytest
from test_timeline import SyntheticVoice, plan

from alice.speech.cli import main
from alice.speech.timeline import AudioClip


@pytest.mark.parametrize("voice,expected", [(None, "azelma"), ("fantine", "fantine")])
def test_cli_uses_alice_voice_by_default_and_preserves_explicit_choice(
    tmp_path, monkeypatch, voice, expected
):
    import alice.speech.cli as cli

    calls = []

    class VoiceSpy:
        identity = {"backend": "synthetic-test"}

        def synthesize(self, text, *, voice, seed):
            calls.append((text, voice, seed))
            return AudioClip(np.zeros(200, dtype=np.float32), 1000)

    monkeypatch.setattr(cli, "PocketSynthesizer", lambda **kwargs: VoiceSpy())
    source = tmp_path / "plan.json"
    document = plan().model_dump(mode="json")
    document.pop("voice")
    if voice is not None:
        document["voice"] = voice
    source.write_text(json.dumps(document))
    output = tmp_path / "result"
    assert main(["--plan", str(source), "--output", str(output)]) == 0
    assert calls == [("Hello.", expected, 7)]
    assert json.loads((output / "manifest.json").read_text())["voice"] == expected


def test_cli_exports_real_audio_timeline_schema_and_manifest(tmp_path, monkeypatch):
    import alice.speech.cli as cli

    monkeypatch.setattr(cli, "PocketSynthesizer", lambda **kwargs: SyntheticVoice())
    source = tmp_path / "plan.json"
    source.write_text(plan().model_dump_json())
    output = tmp_path / "result"
    assert main(["--plan", str(source), "--output", str(output)]) == 0
    with wave.open(str(output / "speech.wav")) as audio:
        assert audio.getframerate() == 1000
        assert audio.getnframes() == 1500
    timeline = json.loads((output / "timeline.json").read_text())
    assert timeline["frames"][0]["sample_index"] == 0
    assert timeline["frames"][-1]["sample_index"] == 1500
    assert timeline["motion"][0]["targets"][0]["actuator_name"] == "mouth_open"
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["actuation_mode"] == "none"
    assert manifest["expression_mode"] == "neutral-fallback"
    for name, digest in manifest["artifacts"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    assert "speech-plan/v1" in (output / "speech-plan.schema.json").read_text()
    # Never overwrite a previous experiment, even with the same plan.
    assert main(["--plan", str(source), "--output", str(output)]) == 2


def test_invalid_plan_never_loads_model_and_preview_escapes_text(tmp_path, monkeypatch):
    import alice.speech.cli as cli
    from alice.speech.artifacts import write_artifacts
    from alice.speech.timeline import prepare_speech

    source = tmp_path / "invalid.json"
    source.write_text('{"segments": []}')
    monkeypatch.setattr(
        cli,
        "PocketSynthesizer",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model must not load")),
    )
    assert main(["--plan", str(source), "--output", str(tmp_path / "bad")]) == 2
    segment = plan().segments[0].model_dump()
    segment["text"] = '</script><script>alert("x")</script>'
    prepared = prepare_speech(plan(segments=[segment]), SyntheticVoice())
    output = tmp_path / "escaped"
    write_artifacts(prepared, output, synthesis_seconds=1)
    html = (output / "preview.html").read_text()
    assert "</script><script>alert" not in html
    assert "data:audio/wav;base64," in html


def test_export_without_git_still_records_manifest(tmp_path, monkeypatch):
    from alice.speech.artifacts import write_artifacts
    from alice.speech.timeline import prepare_speech

    monkeypatch.setenv("PATH", "/missing-tools")
    manifest = write_artifacts(
        prepare_speech(plan(), SyntheticVoice()),
        tmp_path / "no-git",
        synthesis_seconds=1,
    )
    assert manifest["code_revision"] == "unavailable"
    assert (tmp_path / "no-git" / "manifest.json").is_file()
