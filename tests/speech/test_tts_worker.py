"""Real spawned IPC boundary: early delivery, backpressure and fault cleanup."""

import asyncio
import multiprocessing as mp
from dataclasses import dataclass

import numpy as np
import pytest

from alice.contracts.speech_stream import SpeechClause
from alice.speech.timeline import AudioClip
from alice.speech.tts_worker import PcmChunk, PocketTtsWorker


def clause(generation="g1"):
    return SpeechClause(
        generation_id=generation,
        clause_id="c1",
        sequence=0,
        text="Hello.",
        vector=(0, 0, 0),
        intensity=0.5,
        seed=29,
        end_of_response=True,
    )


@dataclass
class FakeBackend:
    gate: object
    produced: object
    fail: bool = False

    @property
    def identity(self):
        return {"backend": "test-chunk-generator"}

    def stream(self, text, *, voice, seed):
        if self.fail:
            raise RuntimeError("injected synthesis failure")
        rng = np.random.default_rng(seed)
        for _ in range(10):
            with self.produced.get_lock():
                self.produced.value += 1
            yield AudioClip(rng.random(240).astype(np.float32), 24000)
        self.gate.wait(10)


@pytest.mark.parametrize(
    "pcm,rate,final",
    [
        ([float("nan")], 24000, False),
        ([[0.1]], 24000, False),
        ([1.1], 24000, False),
        ([0.1], 0, False),
        ([], 24000, False),
    ],
)
def test_invalid_pcm_rejected(pcm, rate, final):
    with pytest.raises(ValueError):
        PcmChunk("g", "c", 0, rate, np.array(pcm, dtype=np.float32), final)


def test_first_chunk_precedes_generator_finish_and_cancellation_kills_stalled_work():
    async def check():
        context = mp.get_context("spawn")
        gate, produced = context.Event(), context.Value("i", 0)
        worker = PocketTtsWorker(capacity=1, backend=FakeBackend(gate, produced))
        stream = worker.stream(clause())
        try:
            first = await asyncio.wait_for(anext(stream), 10)
            assert len(first.pcm) == 240 and not first.final
            assert not gate.is_set()
            assert first.pcm.flags.writeable is False
            # One received, one queued, at most one in the producer's hand.
            assert produced.value <= 3
            await worker.cancel("g1")
            assert not worker.is_alive
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
            gate.set()
            later = [c async for c in worker.stream(clause("g2"))]
            assert all(c.generation_id == "g2" for c in later)
            assert later[-1].final
        finally:
            await stream.aclose()
            await worker.close()
        assert not worker.is_alive

    asyncio.run(check())


def test_warm_worker_keeps_seeded_pcm_equal_and_reports_errors():
    async def check():
        context = mp.get_context("spawn")
        gate, produced = context.Event(), context.Value("i", 0)
        gate.set()
        worker = PocketTtsWorker(backend=FakeBackend(gate, produced))
        try:
            first = [c async for c in worker.stream(clause())]
            second = [c async for c in worker.stream(clause("g2"))]
            assert len(first) == 11
            assert np.array_equal(
                np.concatenate([c.pcm for c in first]),
                np.concatenate([c.pcm for c in second]),
            )
            assert worker.identity["backend"] == "test-chunk-generator"
        finally:
            await worker.close()
        worker = PocketTtsWorker(backend=FakeBackend(gate, produced, fail=True))
        try:
            with pytest.raises(RuntimeError, match="injected synthesis failure"):
                _ = [c async for c in worker.stream(clause())]
        finally:
            await worker.close()

    asyncio.run(check())


def test_live_but_stalled_native_generator_is_killed_on_progress_timeout():
    async def check():
        context = mp.get_context("spawn")
        gate, produced = context.Event(), context.Value("i", 0)
        worker = PocketTtsWorker(
            backend=FakeBackend(gate, produced), chunk_timeout_s=0.1
        )
        try:
            with pytest.raises(RuntimeError, match="progress timeout"):
                _ = [chunk async for chunk in worker.stream(clause())]
            assert not worker.is_alive
        finally:
            await worker.close()

    asyncio.run(check())
