"""Device lifecycle faults must stop output and never report an underflow as done."""

import asyncio
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from alice.contracts.speech import SpeechSyncConfig
from alice.contracts.speech_stream import SpeechClause
from alice.speech.pcm_stream import PcmRingBuffer, PcmTimeline
from alice.speech.stream_playback import SoundDevicePlayback
from alice.speech.tts_worker import PcmChunk


def prepared():
    timeline = PcmTimeline(24000, "g", SpeechSyncConfig(full_open_rms=0.06))
    timeline.commit_clause(
        SpeechClause(
            generation_id="g",
            clause_id="c",
            sequence=0,
            text="Hi",
            vector=(0, 0, 0),
            intensity=0.5,
            seed=0,
            end_of_response=True,
        )
    )
    pcm = np.ones(480, np.float32) * 0.06
    timeline.append(PcmChunk("g", "c", 0, 24000, pcm, True))
    tail = timeline.finish()
    return timeline, np.concatenate((pcm, tail))


def test_cancel_while_device_opens_closes_the_eventual_handle(monkeypatch):
    async def check():
        entered, release = threading.Event(), threading.Event()
        closed = []

        class Stream:
            def __init__(self, **kwargs):
                entered.set()
                release.wait(2)

            def close(self):
                closed.append(True)

        monkeypatch.setitem(
            sys.modules, "sounddevice", SimpleNamespace(OutputStream=Stream)
        )
        timeline, _ = prepared()
        task = asyncio.create_task(
            SoundDevicePlayback()(
                timeline, PcmRingBuffer(24000), lambda frame: None, asyncio.Event(), {}
            )
        )
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == [True]

    asyncio.run(check())


def test_device_underflow_aborts_and_closes(monkeypatch):
    class Abort(Exception):
        pass

    class Stop(Exception):
        pass

    devices = []

    class Stream:
        def __init__(self, **kwargs):
            devices.append(self)
            self.callback = kwargs["callback"]
            self.aborted = self.closed = False
            self.active = False

        def start(self):
            try:
                self.callback(
                    np.empty((480, 1), np.float32),
                    480,
                    SimpleNamespace(outputBufferDacTime=10),
                    "injected output underflow",
                )
            except Abort:
                self.active = False

        def abort(self):
            self.aborted = True

        def close(self):
            self.closed = True

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(OutputStream=Stream, CallbackAbort=Abort, CallbackStop=Stop),
    )

    async def check():
        timeline, pcm = prepared()
        ring = PcmRingBuffer(24000)
        await ring.put(pcm)
        ring.finish()
        metrics = {}
        with pytest.raises(RuntimeError, match="underflow"):
            await SoundDevicePlayback()(
                timeline, ring, lambda frame: None, asyncio.Event(), metrics
            )
        assert devices[0].closed and devices[0].aborted
        assert metrics["underflows"] == 1

    asyncio.run(check())


def test_callback_revokes_shared_signal_while_emit_is_in_flight(monkeypatch):
    signal = threading.Event()
    devices = []

    class Abort(Exception):
        pass

    class Stream:
        def __init__(self, **kwargs):
            devices.append(self)
            self.callback = kwargs["callback"]
            self.active = False
            self.time = 10.0

        def start(self):
            self.active = True
            self.callback(
                np.empty((480, 1), np.float32),
                480,
                SimpleNamespace(outputBufferDacTime=10),
                None,
            )

        def abort(self):
            self.active = False

        def close(self):
            pass

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(
            OutputStream=Stream,
            CallbackAbort=Abort,
            CallbackStop=Exception,
        ),
    )

    async def check():
        timeline, pcm = prepared()
        ring = PcmRingBuffer(24000)
        await ring.put(pcm)
        ring.finish()

        async def emit(frame):
            with pytest.raises(Abort):
                devices[0].callback(
                    np.empty((480, 1), np.float32),
                    480,
                    SimpleNamespace(outputBufferDacTime=10.02),
                    "underflow",
                )
            assert signal.is_set(), "serial authority must revoke before emit returns"
            devices[0].active = False

        with pytest.raises(RuntimeError, match="underflow"):
            await SoundDevicePlayback(fault_signal=signal)(
                timeline, ring, emit, asyncio.Event(), {}
            )

    asyncio.run(check())
