from types import SimpleNamespace

import numpy as np
import pytest

from alice.speech.playback import AudioPlayback
from alice.speech.timeline import AudioClip


class Stop(Exception):
    pass


class Abort(Exception):
    pass


def device():
    return SimpleNamespace(CallbackStop=Stop, CallbackAbort=Abort)


def test_clock_uses_dac_time_not_queued_frames():
    player = AudioPlayback(AudioClip(np.ones(1000, dtype=np.float32) * 0.1, 1000))
    player._device = device()
    buffer = np.zeros((200, 1), dtype=np.float32)
    player._callback(buffer, 200, SimpleNamespace(outputBufferDacTime=10.3), False)
    assert player.sample_position(10.2) is None
    assert player.sample_position(10.35) == 50
    assert np.allclose(buffer[:, 0], 0.1)
    player._callback(buffer, 200, SimpleNamespace(outputBufferDacTime=10.5), False)
    assert player.sample_position(10.4) == 100


def test_underflow_and_final_partial_block_are_explicit():
    player = AudioPlayback(AudioClip(np.ones(50, dtype=np.float32) * 0.2, 1000))
    player._device = device()
    buffer = np.ones((100, 1), dtype=np.float32)
    with pytest.raises(Stop):
        player._callback(buffer, 100, SimpleNamespace(outputBufferDacTime=2), False)
    assert np.allclose(buffer[:50, 0], 0.2)
    assert np.all(buffer[50:, 0] == 0)
    assert player.sample_position(2.1) == 50
    with pytest.raises(Abort):
        player._callback(buffer, 100, SimpleNamespace(outputBufferDacTime=2.1), True)
    assert np.all(buffer == 0)
    assert player.error is not None


def test_cancel_releases_and_closes_stream(monkeypatch):
    import sys
    from threading import Event

    from test_timeline import SyntheticVoice, plan

    from alice.speech.playback import play_speech
    from alice.speech.timeline import prepare_speech

    closed = []
    cancelled = Event()
    cancelled.set()

    class Stream:
        active = True
        time = 0

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def abort(self):
            closed.append("abort")

        def __exit__(self, *args):
            closed.append("closed")

    monkeypatch.setitem(
        sys.modules, "sounddevice", SimpleNamespace(OutputStream=Stream)
    )
    prepared = prepare_speech(plan(), SyntheticVoice())
    emitted = []
    assert play_speech(prepared, emitted.append, cancel=cancelled) == "cancelled"
    assert closed == []
    assert emitted[-1].speech_weight == 0
    assert emitted[-1].sample_index == 0


def test_emit_failure_releases_at_current_expression_time(monkeypatch):
    import sys

    from test_timeline import SyntheticVoice, plan

    from alice.speech.playback import play_speech
    from alice.speech.timeline import prepare_speech

    operations = []

    class Stream:
        active = True
        time = 10.5

        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def __enter__(self):
            self.callback(
                np.zeros((800, 1), dtype=np.float32),
                800,
                SimpleNamespace(outputBufferDacTime=10.0),
                False,
            )
            return self

        def abort(self):
            operations.append("abort")

        def __exit__(self, *args):
            operations.append("close")

    monkeypatch.setitem(
        sys.modules, "sounddevice", SimpleNamespace(OutputStream=Stream)
    )
    prepared = prepare_speech(plan(), SyntheticVoice())
    emitted = []

    def failing_emit(frame):
        emitted.append(frame)
        if frame.speech_weight:
            raise ValueError("consumer failed")

    with pytest.raises(ValueError, match="consumer failed"):
        play_speech(prepared, failing_emit)
    assert emitted[-1].speech_weight == 0
    assert emitted[-1].sample_index == 500
    assert operations == ["abort", "close"]
