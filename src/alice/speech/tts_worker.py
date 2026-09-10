"""One spawned, warm speech model; bounded IPC and generation-scoped results."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import queue
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from alice.contracts.speech_stream import SpeechClause
from alice.speech.synthesis import PocketSynthesizer
from alice.speech.timeline import AudioClip


@dataclass(frozen=True)
class PcmChunk:
    generation_id: str
    clause_id: str
    sequence: int
    sample_rate: int
    pcm: NDArray[np.float32]
    final: bool = False

    def __post_init__(self) -> None:
        pcm = np.array(self.pcm, dtype=np.float32, copy=True)
        if (
            not self.generation_id
            or not self.clause_id
            or type(self.sequence) is not int
            or self.sequence < 0
            or type(self.sample_rate) is not int
            or not 8000 <= self.sample_rate <= 192000
            or pcm.ndim != 1
            or len(pcm) > self.sample_rate
            or (not len(pcm) and not self.final)
            or not np.isfinite(pcm).all()
            or np.any(np.abs(pcm) > 1)
        ):
            raise ValueError("invalid finite mono PCM chunk, rate or identity")
        pcm.setflags(write=False)
        object.__setattr__(self, "pcm", pcm)


class ChunkBackend(Protocol):
    @property
    def identity(self) -> dict[str, str]: ...

    def stream(self, text: str, *, voice: str, seed: int) -> Iterator[AudioClip]: ...


def _worker_main(
    requests: Any, results: Any, backend: ChunkBackend | None, offline: bool
) -> None:
    # This entry point is only called by spawn; it inherits no open device FDs.
    engine = backend or PocketSynthesizer(offline=offline)
    while True:
        request = requests.get()
        if request is None:
            return
        clause = SpeechClause.model_validate(request)
        try:
            sequence, rate = 0, 0
            for clip in engine.stream(clause.text, voice="azelma", seed=clause.seed):
                rate = clip.sample_rate
                # Bound individual IPC messages even if the backend changes size.
                for start in range(0, len(clip.pcm), rate):
                    chunk = PcmChunk(
                        clause.generation_id,
                        clause.clause_id,
                        sequence,
                        rate,
                        clip.pcm[start : start + rate],
                    )
                    results.put(("pcm", chunk))
                    sequence += 1
            if not rate:
                raise RuntimeError("speech backend produced no PCM")
            results.put(("identity", engine.identity))
            results.put(
                (
                    "pcm",
                    PcmChunk(
                        clause.generation_id,
                        clause.clause_id,
                        sequence,
                        rate,
                        np.empty(0, np.float32),
                        True,
                    ),
                )
            )
        except Exception as error:
            results.put(("error", f"{type(error).__name__}: {error}"))


class PocketTtsWorker:
    def __init__(
        self,
        *,
        capacity: int = 4,
        offline: bool = True,
        backend: ChunkBackend | None = None,
        shutdown_timeout_s: float = 0.5,
    ) -> None:
        if not 1 <= capacity <= 32 or not 0 < shutdown_timeout_s <= 5:
            raise ValueError("invalid worker queue or shutdown bound")
        self._capacity = capacity
        self._offline = offline
        self._backend = backend
        self._deadline = shutdown_timeout_s
        self._process: Any = None
        self._requests: Any = None
        self._results: Any = None
        self._serial = asyncio.Lock()
        self._lifecycle = asyncio.Lock()
        self._active: str | None = None
        self._cancelled: str | None = None
        self._closed = False
        self.identity: dict[str, str] = {}

    @property
    def is_alive(self) -> bool:
        return self._process is not None and bool(self._process.is_alive())

    def _start(self) -> None:
        if self.is_alive:
            return
        context = mp.get_context("spawn")
        self._requests = context.Queue(maxsize=1)
        self._results = context.Queue(maxsize=self._capacity)
        self._process = context.Process(
            target=_worker_main,
            args=(self._requests, self._results, self._backend, self._offline),
            daemon=True,
        )
        self._process.start()

    async def stream(self, clause: SpeechClause) -> AsyncIterator[PcmChunk]:
        async with self._serial:
            if self._closed:
                raise RuntimeError("speech worker is closed")
            if clause.generation_id == self._cancelled:
                return
            async with self._lifecycle:
                self._start()
                self._active = clause.generation_id
                self._requests.put_nowait(clause.model_dump())
            expected, rate, finished = 0, None, False
            try:
                while self._active == clause.generation_id:
                    try:
                        kind, payload = self._results.get_nowait()
                    except queue.Empty:
                        if not self.is_alive:
                            raise RuntimeError("speech worker exited unexpectedly")
                        await asyncio.sleep(0.002)
                        continue
                    if kind == "error":
                        raise RuntimeError(str(payload))
                    if kind == "identity":
                        self.identity = dict(payload)
                        continue
                    if kind != "pcm" or not isinstance(payload, PcmChunk):
                        raise RuntimeError("invalid speech worker message")
                    # Unpickling bypasses dataclass validation/write protection.
                    chunk = PcmChunk(
                        payload.generation_id,
                        payload.clause_id,
                        payload.sequence,
                        payload.sample_rate,
                        payload.pcm,
                        payload.final,
                    )
                    if (
                        chunk.generation_id != clause.generation_id
                        or chunk.clause_id != clause.clause_id
                        or chunk.sequence != expected
                        or rate not in (None, chunk.sample_rate)
                    ):
                        raise RuntimeError("stale or out-of-order speech worker PCM")
                    expected += 1
                    rate = chunk.sample_rate
                    finished = chunk.final
                    yield chunk
                    if finished:
                        return
            finally:
                if not finished and self._active == clause.generation_id:
                    await self.cancel(clause.generation_id)
                if self._active == clause.generation_id:
                    self._active = None

    async def cancel(self, generation_id: str) -> None:
        self._cancelled = generation_id
        if self._active != generation_id:
            return
        self._active = None  # invalidate before awaiting any process operation
        await self._stop()

    async def _stop(self) -> None:
        async with self._lifecycle:
            process = self._process
            if process is None:
                return
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, self._deadline)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, self._deadline)
            if process.is_alive():
                raise RuntimeError("speech worker did not stop within deadline")
            process.close()
            self._process = None
            for channel in (self._requests, self._results):
                channel.cancel_join_thread()
                channel.close()
            self._requests = self._results = None

    async def close(self) -> None:
        self._closed = True
        self._active = None
        await self._stop()
