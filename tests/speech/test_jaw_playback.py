import time
from threading import Event

import numpy as np
import pytest
from test_jaw_trial import running_stream

from alice.contracts.speech import SpeechPlan
from alice.speech.timeline import AudioClip, prepare_speech


def short_speech():
    class Voice:
        identity = {"backend": "test"}

        def synthesize(self, *args, **kwargs):
            return AudioClip(np.ones(100, dtype=np.float32) * 0.1, 1000)

    plan = SpeechPlan.model_validate(
        {
            "schema_version": "speech-plan/v1",
            "utterance_id": "test",
            "source_id": "test",
            "segments": [
                {
                    "text": "Test.",
                    "cues": [{"progress": 0, "vector": [0, 0, 0], "intensity": 0}],
                }
            ],
        }
    )
    return prepare_speech(plan, Voice())


def test_playback_failure_never_runs_normal_home_return():
    from alice.speech.jaw_playback import run_jaw_playback

    stream, now, supervisor, adapter, _ = running_stream()

    def sleep(seconds):
        now[0] += round(seconds * 1e9)
        time.sleep(0.00005)

    def failed_player(prepared, emit, *, cancel):
        emit(prepared.frames[-1])  # failure and success both emit a release
        raise RuntimeError("injected underflow")

    with pytest.raises(RuntimeError, match="underflow"):
        run_jaw_playback(short_speech(), stream, player=failed_player, sleeper=sleep)
    assert supervisor.state.value == "faulted"
    assert adapter.positions["mouth_open"] < -0.99
    assert all(record["audio_sample"] is None for record in stream.records)


def test_precancel_does_not_move_or_open_audio():
    from alice.speech.jaw_playback import run_jaw_playback

    stream, _, _, adapter, _ = running_stream()
    cancel = Event()
    cancel.set()

    def forbidden(*args, **kwargs):
        pytest.fail("cancelled trial must not open audio")

    with pytest.raises(RuntimeError, match="cancelled"):
        run_jaw_playback(short_speech(), stream, cancel=cancel, player=forbidden)
    assert not adapter.positions


@pytest.mark.parametrize("delay_s", [0, 0.025, 0.04])
def test_completed_audio_returns_exact_home_and_retains_trace(delay_s):
    from alice.speech.jaw_playback import run_jaw_playback
    from alice.speech.jaw_trial import JawTrialConfig

    stream, now, supervisor, adapter, _ = running_stream(
        delay_s,
        initial_position=-0.99 if delay_s == 0.04 else 0.0,
        config=JawTrialConfig(phase_timeout_s=8),
    )
    telemetry = {}

    def sleep(seconds):
        now[0] += round(seconds * 1e9)
        time.sleep(0.00005)

    def player(prepared, emit, *, cancel):
        for frame in prepared.frames:
            emit(frame)
            time.sleep(0.002)
        return "completed"

    result = run_jaw_playback(
        short_speech(), stream, player=player, sleeper=sleep, telemetry=telemetry
    )
    assert result is telemetry
    assert result["audio_outcome"] == "completed"
    assert result["controller_home_confirmed"]
    assert adapter.positions["mouth_open"] == 0
    assert set(adapter.positions) == {"mouth_open"}
    assert supervisor.state.value == "disarmed"
    assert any(r["audio_sample"] is not None for r in stream.records)


def test_audio_error_survives_close_error_and_retains_failure_trace(monkeypatch):
    from alice.speech.jaw_playback import run_jaw_playback

    stream, now, _, _, boundary = running_stream()
    telemetry = {}

    def sleep(seconds):
        now[0] += round(seconds * 1e9)
        time.sleep(0.00005)

    def player(prepared, emit, *, cancel):
        emit(prepared.frames[-1])
        raise RuntimeError("original audio fault")

    def bad_close():
        raise RuntimeError("close failed")

    monkeypatch.setattr(boundary, "close", bad_close)
    with pytest.raises(RuntimeError, match="original audio fault"):
        run_jaw_playback(
            short_speech(), stream, player=player, sleeper=sleep, telemetry=telemetry
        )
    assert telemetry["audio_outcome"] == "failed"
    assert telemetry["audio_frames"]
    assert "close failed" in str(telemetry["cleanup_errors"])


def test_stalled_audio_aborts_without_home_return():
    from alice.speech.jaw_playback import run_jaw_playback

    stream, now, supervisor, adapter, _ = running_stream()

    def sleep(seconds):
        now[0] += round(seconds * 1e9)
        time.sleep(0.00005)

    def stalled(prepared, emit, *, cancel):
        emit(prepared.frames[0])
        cancel.wait(2)
        return "cancelled"

    with pytest.raises(RuntimeError, match="source stalled"):
        run_jaw_playback(short_speech(), stream, player=stalled, sleeper=sleep)
    assert supervisor.state.value == "faulted"
    assert adapter.positions["mouth_open"] != 0


def test_full_motion_proposal_maps_all_servos_and_selects_only_jaw():
    from pathlib import Path

    from alice.contracts.motion import TargetUpdate
    from alice.hardware.manifest import load_manifest
    from alice.speech.jaw_playback import select_motion_targets

    manifest = load_manifest(Path(__file__).parents[2] / "hardware/alice-face-v1.yaml")
    update = TargetUpdate.model_validate(
        {
            "offset_s": 0,
            "targets": [
                {"actuator_name": a.name, "normalized_position": 0.1}
                for a in manifest.actuators
            ],
        }
    )
    all_names = tuple(a.name for a in manifest.actuators)
    assert len(select_motion_targets(update, manifest, all_names).targets) == 11
    jaw = select_motion_targets(update, manifest, ("mouth_open",))
    assert tuple(t.actuator_name for t in jaw.targets) == ("mouth_open",)


def test_mouth_lead_uses_future_aperture_but_keeps_current_audio_clock():
    from dataclasses import replace

    from alice.speech.jaw_playback import run_jaw_playback
    from alice.speech.jaw_trial import JawTrialConfig

    prepared = short_speech()
    frames = tuple(
        frame.model_copy(
            update={"mouth_aperture": 0.0 if frame.sample_index < 80 else 1.0}
        )
        if frame.speech_weight
        else frame
        for frame in prepared.frames
    )
    prepared = replace(prepared, frames=frames)
    stream, now, _, _, _ = running_stream(config=JawTrialConfig(mouth_lead_s=0.1))
    observed = Event()
    step = stream.step

    def capture(desired, *, audio_sample):
        result = step(desired, audio_sample=audio_sample)
        if audio_sample is not None and result is not None:
            observed.set()
        return result

    stream.step = capture

    def player(speech, emit, *, cancel):
        emit(speech.frames[0])
        assert observed.wait(2)
        return "completed"

    def sleep(seconds):
        now[0] += round(seconds * 1e9)
        time.sleep(0.00005)

    result = run_jaw_playback(prepared, stream, player=player, sleeper=sleep)
    proposal = result["composed_proposals"][0]
    assert proposal["offset_s"] == 0.0
    assert proposal["targets"][0]["normalized_position"] == pytest.approx(0.6)
    assert result["audio_frames"][0]["frame"]["mouth_aperture"] == 0
    assert result["audio_frames"][0]["frame"]["sample_index"] == 0
    assert result["mouth_lead_s"] == 0.1
    assert result["controller_home_confirmed"]
