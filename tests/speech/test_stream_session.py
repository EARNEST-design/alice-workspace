"""Event barriers establish genuine overlap, independent of machine speed."""

import asyncio

import numpy as np
import pytest

from alice.contracts.speech_stream import SpeechClause
from alice.speech.stream_session import SpeechStreamSession
from alice.speech.tts_worker import PcmChunk


def clause(sequence=0):
    return SpeechClause(
        generation_id="g",
        clause_id=f"c{sequence}",
        sequence=sequence,
        text="Hi.",
        seed=29,
        vector=(0.6 if sequence == 0 else -0.6, 0, 0),
        intensity=0.7,
        end_of_response=sequence == 1,
    )


class Worker:
    identity = {"backend": "test-incremental"}

    def __init__(self, gate):
        self.gate = gate
        self.cancelled = []

    async def stream(self, clause):
        yield PcmChunk("g", clause.clause_id, 0, 24000, np.full(7680, 0.04, np.float32))
        await self.gate.wait()
        yield PcmChunk(
            "g", clause.clause_id, 1, 24000, np.full(4800, 0.04, np.float32), True
        )

    async def cancel(self, generation):
        self.cancelled.append(generation)


class EventPlayback:
    def __init__(self, first, release):
        self.first, self.release = first, release

    async def __call__(self, timeline, ring, emit, cancel, metrics):
        # Model a real first audible callback: consume committed PCM before
        # signaling the observer. Dispatching a frame alone is not playback.
        output = np.empty((480, 1), np.float32)
        assert ring.read_into(output) == 480
        assert np.any(output)
        sample = 480
        await emit(timeline.led_frame(0))
        self.first.set()
        await self.release.wait()
        while not ring.finished or ring.depth:
            if ring.depth:
                count = min(480, ring.depth)
                ring.read_into(np.empty((count, 1), np.float32))
                sample += count
                # Test device advances only when lookahead is available.
                if timeline.finished or sample + 2400 < timeline.envelope_samples:
                    await emit(timeline.led_frame(sample))
            await asyncio.sleep(0)
        metrics["played_samples"] = sample


def test_audio_begins_before_both_text_and_first_clause_tts_finish():
    async def check():
        tts_gate, llm_gate = asyncio.Event(), asyncio.Event()
        first, release = asyncio.Event(), asyncio.Event()
        frames = []

        async def source():
            yield clause()
            await llm_gate.wait()
            yield clause(1)

        session = SpeechStreamSession(
            worker=Worker(tts_gate),
            emit=frames.append,
            playback=EventPlayback(first, release),
        )
        task = asyncio.create_task(session.run(source(), asyncio.Event()))
        await asyncio.wait_for(first.wait(), 2)
        assert not tts_gate.is_set() and not llm_gate.is_set()
        assert frames[0].vector[0] == 0.6
        tts_gate.set()
        llm_gate.set()
        release.set()
        result = await asyncio.wait_for(task, 2)
        assert result["outcome"] == "completed"
        assert result["max_buffer_samples"] <= 48000
        assert frames[-1].speech_weight == 0
        assert sum(f.speech_weight == 0 for f in frames) == 1
        assert any(f.vector[0] < 0 for f in frames)

    asyncio.run(check())


def test_cancel_flushes_source_worker_audio_and_releases_once():
    async def check():
        gate, first, release, cancel = (asyncio.Event() for _ in range(4))
        frames, worker = [], Worker(gate)

        async def source():
            yield clause()
            await gate.wait()
            yield clause(1)

        session = SpeechStreamSession(
            worker=worker, emit=frames.append, playback=EventPlayback(first, release)
        )
        task = asyncio.create_task(session.run(source(), cancel))
        await asyncio.wait_for(first.wait(), 2)
        cancel.set()
        result = await asyncio.wait_for(task, 2)
        assert result["outcome"] == "cancelled"
        assert worker.cancelled == ["g"]
        assert session.ring.depth == 0
        assert sum(f.speech_weight == 0 for f in frames) == 1

    asyncio.run(check())


def test_worker_failure_propagates_and_releases_ownership():
    class Failing(Worker):
        async def stream(self, clause):
            yield PcmChunk("g", clause.clause_id, 0, 24000, np.ones(480, np.float32))
            raise RuntimeError("injected worker failure")

    async def check():
        frames = []

        async def source():
            yield clause()
            yield clause(1)

        session = SpeechStreamSession(
            worker=Failing(asyncio.Event()), emit=frames.append
        )
        with pytest.raises(RuntimeError, match="injected worker failure"):
            await asyncio.wait_for(session.run(source(), asyncio.Event()), 2)
        assert len(frames) == 1 and frames[0].speech_weight == 0
        assert session.ring.depth == 0

    asyncio.run(check())


def test_chunk_larger_than_ring_still_starts_playback():
    async def check():
        gate, first, release = (asyncio.Event() for _ in range(3))
        gate.set()
        release.set()

        async def source():
            yield clause()
            yield clause(1)

        session = SpeechStreamSession(
            worker=Worker(gate),
            emit=lambda frame: None,
            capacity_s=0.2,
            playback=EventPlayback(first, release),
        )
        result = await asyncio.wait_for(session.run(source(), asyncio.Event()), 2)
        assert result["outcome"] == "completed"
        assert result["max_buffer_samples"] <= 4800

    asyncio.run(check())


def test_cancel_joins_inflight_consumer_before_terminal_release():
    import threading

    async def check():
        entered, release = threading.Event(), threading.Event()
        cancel, gate = asyncio.Event(), asyncio.Event()
        frames = []

        def emit(frame):
            if frame.speech_weight:
                entered.set()
                release.wait(2)
            frames.append(frame)

        async def source():
            yield clause()
            await gate.wait()
            yield clause(1)

        session = SpeechStreamSession(worker=Worker(gate), emit=emit)
        task = asyncio.create_task(session.run(source(), cancel))
        assert await asyncio.to_thread(entered.wait, 2)
        cancel.set()

        # A barrier in worker.cancel exposes whether finalization overtook emit.
        async def cancel_worker(generation):
            assert not frames or frames[-1].speech_weight > 0
            release.set()

        session.worker.cancel = cancel_worker
        # Release the consumer explicitly without relying on its timeout.
        asyncio.get_running_loop().call_later(0.05, release.set)
        await asyncio.wait_for(task, 2)
        assert len(frames) == 2 and frames[-1].speech_weight == 0

    asyncio.run(check())


def test_cancel_invalidates_audio_before_joining_a_blocked_consumer():
    import threading

    async def check():
        entered, release = threading.Event(), threading.Event()
        invalidated, cancel, gate = (asyncio.Event() for _ in range(3))

        def emit(frame):
            if frame.speech_weight:
                entered.set()
                release.wait(3)

        async def source():
            yield clause()
            await gate.wait()
            yield clause(1)

        session = SpeechStreamSession(worker=Worker(gate), emit=emit)
        task = asyncio.create_task(session.run(source(), cancel))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            original = session.ring.abort

            def abort():
                original()
                invalidated.set()

            session.ring.abort = abort
            cancel.set()
            await asyncio.wait_for(invalidated.wait(), 0.5)
            assert session.ring.depth == 0
            assert not release.is_set()
        finally:
            release.set()
            await asyncio.wait_for(task, 2)

    asyncio.run(check())
