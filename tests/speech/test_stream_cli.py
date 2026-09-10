"""Run the user-facing incremental entry point and validate retained artifacts."""

import hashlib
import json
import wave

import numpy as np

from alice.speech.cli import main
from alice.speech.tts_worker import PcmChunk


def install_worker(monkeypatch):
    from alice.speech import stream_cli

    class Worker:
        identity = {"backend": "test-stream"}

        def __init__(self, **kwargs):
            pass

        async def stream(self, clause):
            yield PcmChunk(
                clause.generation_id,
                clause.clause_id,
                0,
                24000,
                np.full(4800, 0.04, np.float32),
                True,
            )

        async def cancel(self, generation):
            pass

        async def close(self):
            pass

    monkeypatch.setattr(stream_cli, "PocketTtsWorker", Worker)


def test_stream_cli_retains_generated_pcm_and_composed_provenance(
    tmp_path, monkeypatch
):
    install_worker(monkeypatch)
    output = tmp_path / "run"
    assert (
        main(
            [
                "--clauses",
                "config/speech/stream-demo-v1.jsonl",
                "--output",
                str(output),
                "--expression-mode",
                "authored",
            ]
        )
        == 0
    )
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["actuation_mode"] == "none"
    assert manifest["expression"]["source"] == "authored-expression/v1"
    assert manifest["metrics"]["outcome"] == "completed"
    assert manifest["metrics"]["clock_kind"] == "simulated-dac"
    assert manifest["metrics"]["generated_samples"] == 16800
    for name, digest in manifest["artifacts"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    with wave.open(str(output / "generated.wav")) as audio:
        assert audio.getnframes() == 16800
    rows = [
        json.loads(line)
        for line in (output / "composed.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["frame"]["speech_weight"] == 0
    assert len(rows[0]["proposal"]["targets"]) == 11
    assert (output / "preview.html").is_file()
    assert (
        main(
            ["--clauses", "config/speech/stream-demo-v1.jsonl", "--output", str(output)]
        )
        == 2
    )


def test_stream_failure_keeps_original_error_release_and_manifest(
    tmp_path, monkeypatch, capsys
):
    from alice.speech import stream_cli

    install_worker(monkeypatch)

    class FailingPlayback:
        async def __call__(self, timeline, ring, emit, cancel, metrics):
            await emit(timeline.led_frame(480))
            await emit(timeline.led_frame(960))
            raise RuntimeError("original device failure")

    monkeypatch.setattr(stream_cli, "SoundDevicePlayback", FailingPlayback)
    output = tmp_path / "failed"
    assert (
        main(
            [
                "--clauses",
                "config/speech/stream-demo-v1.jsonl",
                "--output",
                str(output),
                "--play",
            ]
        )
        == 2
    )
    assert "original device failure" in capsys.readouterr().err
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["metrics"]["outcome"] == "failed"
    assert manifest["metrics"]["error"] == "original device failure"
    rows = [
        json.loads(line)
        for line in (output / "composed.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["frame"]["sample_index"] == rows[-2]["frame"]["sample_index"]
    assert rows[-1]["frame"]["speech_weight"] == 0
