"""Async orchestration for committed clauses, warm TTS and bounded playback."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from typing import Protocol

from alice.contracts.speech import SpeechSyncConfig
from alice.contracts.speech_stream import ClauseSequence, SpeechClause
from alice.speech.pcm_stream import PcmRingBuffer, PcmTimeline
from alice.speech.timeline import SpeechFrame
from alice.speech.tts_worker import PcmChunk


class SpeechWorker(Protocol):
    @property
    def identity(self) -> dict[str, str]: ...

    def stream(self, clause: SpeechClause) -> AsyncIterator[PcmChunk]: ...
    async def cancel(self, generation_id: str) -> None: ...


class PlaybackDriver(Protocol):
    async def __call__(
        self,
        timeline: PcmTimeline,
        ring: PcmRingBuffer,
        emit: Callable[[SpeechFrame], Awaitable[None]],
        cancel: asyncio.Event,
        metrics: dict[str, object],
    ) -> None: ...


class SpeechStreamSession:
    def __init__(
        self,
        *,
        worker: SpeechWorker,
        emit: Callable[[SpeechFrame], None],
        config: SpeechSyncConfig | None = None,
        playback: PlaybackDriver | None = None,
        prebuffer_s: float = 0.2,
        capacity_s: float = 2.0,
    ) -> None:
        if not 0.2 <= prebuffer_s <= capacity_s <= 2.0:
            raise ValueError("invalid prebuffer or PCM capacity")
        self.worker, self.emit = worker, emit
        self.config = config or SpeechSyncConfig(full_open_rms=0.06, open_position=1)
        if playback is None:
            from alice.speech.stream_playback import SimulatedPlayback

            playback = SimulatedPlayback()
        self.playback = playback
        self.prebuffer_s, self.capacity_s = prebuffer_s, capacity_s
        self.ring: PcmRingBuffer | None = None
        self.timeline: PcmTimeline | None = None
        self.metrics: dict[str, object] = {}
        self._used = False

    async def run(
        self, clauses: AsyncIterable[SpeechClause], cancel: asyncio.Event
    ) -> dict[str, object]:
        if self._used:
            raise RuntimeError("create a fresh session for each generation")
        self._used = True
        started = time.monotonic()
        ledger = ClauseSequence()
        queue: asyncio.Queue[SpeechClause | None] = asyncio.Queue(maxsize=2)
        ready = asyncio.Event()
        last_sample = 0
        self.metrics = {
            "outcome": "running",
            "mouth_lead_s": 0.1,
            "prebuffer_s": self.prebuffer_s,
            "underflows": 0,
            "first_audio_latency_s": None,
            "first_pcm_latency_s": None,
        }

        async def emit(frame: SpeechFrame) -> None:
            nonlocal last_sample
            if cancel.is_set() or frame.speech_weight == 0:
                return
            last_sample = frame.sample_index
            if self.metrics["first_audio_latency_s"] is None:
                self.metrics["first_audio_latency_s"] = time.monotonic() - started
            # Inference/servo consumers never block the asyncio loop or callback.
            # Coalesce in the driver by reading its current DAC position again.
            emission = asyncio.create_task(asyncio.to_thread(self.emit, frame))
            try:
                await asyncio.shield(emission)
            except asyncio.CancelledError:
                # A thread cannot be cancelled. Join it before terminal release
                # so an obsolete consumer cannot emit after this generation ends.
                await emission
                raise

        async def source() -> None:
            async for clause in clauses:
                ledger.commit(clause)
                await queue.put(clause)
            ledger.finish()
            await queue.put(None)

        async def synthesize() -> None:
            async def enqueue(pcm: PcmChunk | None = None) -> None:
                assert self.timeline is not None and self.ring is not None
                samples = pcm.pcm if pcm is not None else self.timeline.finish()
                rate = self.timeline.sample_rate
                hop = round(rate * 0.02)
                for offset in range(0, len(samples), hop):
                    await self.ring.put(samples[offset : offset + hop])
                    if self.ring.depth >= round(self.prebuffer_s * rate):
                        ready.set()

            while (clause := await queue.get()) is not None:
                committed = False
                async for chunk in self.worker.stream(clause):
                    if cancel.is_set():
                        return
                    if self.timeline is None:
                        self.timeline = PcmTimeline(
                            chunk.sample_rate, clause.generation_id, self.config
                        )
                        self.ring = PcmRingBuffer(
                            round(self.capacity_s * chunk.sample_rate)
                        )
                    assert self.ring is not None
                    if not committed:
                        self.timeline.commit_clause(clause)
                        committed = True
                    self.timeline.append(chunk)
                    if len(chunk.pcm) and self.metrics["first_pcm_latency_s"] is None:
                        self.metrics["first_pcm_latency_s"] = time.monotonic() - started
                    await enqueue(chunk)
                if not committed:
                    raise RuntimeError("worker returned no clause PCM")
            if self.timeline is None or self.ring is None:
                raise ValueError("empty speech stream")
            await enqueue()
            self.ring.finish()
            ready.set()  # includes an utterance shorter than the initial buffer

        async def play() -> None:
            await ready.wait()
            assert self.timeline is not None and self.ring is not None
            await self.playback(self.timeline, self.ring, emit, cancel, self.metrics)

        async def pipeline() -> None:
            async with asyncio.TaskGroup() as group:
                group.create_task(source())
                group.create_task(synthesize())
                group.create_task(play())

        work = asyncio.create_task(pipeline())
        cancelled = asyncio.create_task(cancel.wait())
        try:
            await asyncio.wait((work, cancelled), return_when=asyncio.FIRST_COMPLETED)
            if cancel.is_set():
                self.metrics["outcome"] = "cancelled"
                work.cancel()
            else:
                try:
                    await work
                except ExceptionGroup as error:
                    # Expose the originating fault while TaskGroup joins siblings.
                    raise error.exceptions[0] from error
                self.metrics["outcome"] = "completed"
            return self.metrics
        except BaseException as error:
            self.metrics.update(outcome="failed", error=str(error))
            raise
        finally:
            cancelled.cancel()
            work.cancel()
            await asyncio.gather(work, cancelled, return_exceptions=True)
            if self.metrics["outcome"] != "completed":
                if self.ring is not None:
                    self.ring.abort()
                if ledger.generation_id is not None:
                    await self.worker.cancel(ledger.generation_id)
            # Exactly one terminal release; drivers never emit the terminal frame.
            end = (
                self.timeline.generated_samples
                if self.timeline is not None and self.metrics["outcome"] == "completed"
                else last_sample
            )
            await asyncio.to_thread(
                self.emit,
                SpeechFrame(
                    sample_index=end,
                    mouth_aperture=0,
                    speech_weight=0,
                    vector=(0, 0, 0),
                    intensity=0,
                ),
            )
            self.metrics.update(
                duration_s=time.monotonic() - started,
                generated_samples=self.timeline.generated_samples
                if self.timeline
                else 0,
                max_buffer_samples=self.ring.max_depth if self.ring else 0,
                played_samples=self.metrics.get("played_samples", last_sample),
                model_identity=dict(self.worker.identity),
            )
