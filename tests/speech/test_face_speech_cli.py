"""The composed trial defaults to simulated servos and retains fault evidence."""

import asyncio
import json
import shutil
import time

import numpy as np
import pytest
from test_face_stream import ROOT

from alice.speech.tts_worker import PcmChunk


class ShortWorker:
    identity = {"model": "test PCM"}

    def __init__(self, **kwargs):
        pass

    async def stream(self, clause):
        yield PcmChunk(
            clause.generation_id,
            clause.clause_id,
            0,
            24000,
            np.ones(4800, dtype=np.float32) * 0.06,
            True,
        )

    async def cancel(self, generation_id):
        pass

    async def close(self):
        pass


def test_default_trial_runs_real_composer_and_guards_without_devices(
    tmp_path, monkeypatch
):
    from alice.experiments import face_speech_cli as cli

    monkeypatch.setattr(cli, "PocketTtsWorker", ShortWorker)

    def no_hardware(*args):
        pytest.fail("default trial opened hardware")

    monkeypatch.setattr(cli, "_check_owners", no_hardware)
    output = tmp_path / "run"
    assert (
        cli.main(
            [
                "--clauses",
                str(ROOT / "config/speech/stream-demo-v1.jsonl"),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    report = json.loads((output / "manifest.json").read_text())
    assert report["outcome"] == "completed"
    assert report["actuation_mode"] == "simulated"
    assert report["controller_home_confirmed"]
    records = [
        json.loads(line)
        for line in (output / "commands.jsonl").read_text().splitlines()
    ]
    assert {
        r["request"]["targets"][0]["actuator_name"] for r in records if "request" in r
    } == {
        "mouth_open",
        "lower_eyelids",
        "upper_eyelids",
        "forehead_frown",
        "left_mouth_corner",
        "right_mouth_corner",
    }
    assert report["audio"]["mouth_lead_s"] == 0.1


def test_failed_audio_closes_face_without_home_recovery(tmp_path, monkeypatch):
    from alice.experiments import face_speech_cli as cli

    monkeypatch.setattr(cli, "PocketTtsWorker", ShortWorker)

    class FailPlayback:
        async def __call__(self, timeline, ring, emit, cancel, metrics):
            await emit(timeline.frame_at(0))
            await asyncio.sleep(0.08)
            raise RuntimeError("injected audio failure")

    monkeypatch.setattr(cli, "SimulatedPlayback", FailPlayback)
    output = tmp_path / "failed"
    assert (
        cli.main(
            [
                "--clauses",
                str(ROOT / "config/speech/stream-demo-v1.jsonl"),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    report = json.loads((output / "manifest.json").read_text())
    assert report["outcome"] == "failed"
    assert "injected audio failure" in report["error"]
    assert not report["controller_home_confirmed"]
    assert report["artifacts"]["commands.jsonl"]


def test_cleanup_error_retains_audio_and_command_evidence(tmp_path, monkeypatch):
    from alice.experiments import face_speech_cli as cli

    class BadClose(ShortWorker):
        async def close(self):
            raise RuntimeError("injected worker close")

    monkeypatch.setattr(cli, "PocketTtsWorker", BadClose)
    output = tmp_path / "cleanup"
    assert (
        cli.main(
            [
                "--clauses",
                str(ROOT / "config/speech/stream-demo-v1.jsonl"),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    report = json.loads((output / "manifest.json").read_text())
    assert report["artifacts"]["commands.jsonl"]
    assert report["artifacts"]["audio-metrics.json"]
    assert "worker close" in str(report["cleanup_errors"])


def test_audio_fault_revokes_face_before_slow_worker_cancel(tmp_path, monkeypatch):
    from alice.experiments import face_speech_cli as cli

    fault = []

    class SlowCancel(ShortWorker):
        async def cancel(self, generation_id):
            await asyncio.sleep(0.2)

    class FailPlayback:
        async def __call__(self, timeline, ring, emit, cancel, metrics):
            await emit(timeline.led_frame(0))
            await asyncio.sleep(0.05)
            fault.append(time.monotonic_ns())
            raise RuntimeError("immediate audio fault")

    monkeypatch.setattr(cli, "PocketTtsWorker", SlowCancel)
    monkeypatch.setattr(cli, "SimulatedPlayback", FailPlayback)
    output = tmp_path / "fault"
    assert (
        cli.main(
            [
                "--clauses",
                str(ROOT / "config/speech/stream-demo-v1.jsonl"),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    records = [
        json.loads(r) for r in (output / "commands.jsonl").read_text().splitlines()
    ]
    assert records
    assert not [
        r
        for r in records
        if "request" in r and r["request"]["issued_monotonic_ns"] > fault[0]
    ]


def test_trial_uses_snapshotted_alternate_config_root(tmp_path, monkeypatch):
    from alice.experiments import face_speech_cli as cli

    monkeypatch.setattr(cli, "PocketTtsWorker", ShortWorker)
    config = tmp_path / "config"
    shutil.copytree(ROOT / "config", config)
    sync = json.loads((config / "speech/sync-hardware-v1.json").read_text())
    sync["open_position"] = 0.5
    sync["expression_jaw_weight"] = 0
    (config / "speech/sync-hardware-v1.json").write_text(json.dumps(sync))
    output = tmp_path / "configured"
    assert (
        cli.main(
            [
                "--clauses",
                str(ROOT / "config/speech/stream-demo-v1.jsonl"),
                "--config-root",
                str(config),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    rows = [json.loads(r) for r in (output / "composed.jsonl").read_text().splitlines()]
    jaw = [
        t["normalized_position"]
        for r in rows
        if "proposal" in r
        for t in r["proposal"]["targets"]
        if t["actuator_name"] == "mouth_open"
    ]
    assert 0.4 < max(jaw) <= 0.5
    assert (
        json.loads((output / "config/speech/sync-hardware-v1.json").read_text())[
            "open_position"
        ]
        == 0.5
    )


def test_successful_sad_line_holds_closed_frown_after_audio(tmp_path, monkeypatch):
    from alice.experiments import face_speech_cli as cli

    monkeypatch.setattr(cli, "PocketTtsWorker", ShortWorker)
    output = tmp_path / "sad"
    assert (
        cli.main(
            [
                "--clauses",
                str(ROOT / "config/speech/stream-visible-demo-v1.jsonl"),
                "--output",
                str(output),
                "--sad-hold-s",
                ".1",
            ]
        )
        == 0
    )
    records = [
        json.loads(r) for r in (output / "commands.jsonl").read_text().splitlines()
    ]
    post = [r for r in records if r.get("phase") == "post-speech" and "status" in r]
    assert post
    assert all(r["audio_sample"] is None for r in post)
    targets = {
        r["request"]["targets"][0]["actuator_name"]: r["status"]["target_qus"]
        for r in post
    }
    assert targets["mouth_open"] == 4608
    assert targets["left_mouth_corner"] < 5500
    assert targets["right_mouth_corner"] > 6500
    assert json.loads((output / "manifest.json").read_text())[
        "controller_home_confirmed"
    ]
