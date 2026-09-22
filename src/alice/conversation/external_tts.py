"""Bounded binary IPC adapter for an isolated local speech runtime."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import TypeVar

import numpy as np

from alice.contracts.speech_stream import SpeechClause
from alice.speech.tts_worker import PcmChunk

_T = TypeVar("_T")
_HEADER_LIMIT = 1024
_SAMPLE_RATE = 24_000
_MAX_CHUNK_SAMPLES = 24_000
_MAX_TOTAL_SAMPLES = 360_000


class _WorkerCancelled(Exception):
    """The lifecycle owner changed while an operation was awaiting I/O."""


async def _terminate(
    process: asyncio.subprocess.Process | None, timeout_s: float
) -> None:
    if process is None:
        return
    if process.stdin is not None:
        process.stdin.close()
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout_s)
        return
    except TimeoutError:
        pass
    try:
        process.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout_s)
    except TimeoutError as error:
        raise RuntimeError("external speech worker did not stop") from error


class ExternalTtsWorker:
    """Own one persistent external model process with generation-safe cleanup."""

    def __init__(
        self,
        python: Path,
        script: Path,
        model: Path,
        source: Path,
        *,
        startup_timeout_s: float = 90.0,
        chunk_timeout_s: float = 10.0,
        shutdown_timeout_s: float = 0.5,
        total_timeout_s: float = 120.0,
    ) -> None:
        bounds = (
            (startup_timeout_s, 90.0),
            (chunk_timeout_s, 10.0),
            (shutdown_timeout_s, 0.5),
            (total_timeout_s, 180.0),
        )
        if not all(
            math.isfinite(value) and 0 < value <= upper for value, upper in bounds
        ):
            raise ValueError("worker timeouts must be finite, positive and bounded")
        self._python = python
        self._script = script
        self._model = model
        self._source = source
        self._startup_timeout = startup_timeout_s
        self._chunk_timeout = chunk_timeout_s
        self._shutdown_timeout = shutdown_timeout_s
        self._total_timeout = total_timeout_s
        self._serial = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._spawn_task: asyncio.Task[asyncio.subprocess.Process] | None = None
        self._reapers: set[asyncio.Task[None]] = set()
        self._active: str | None = None
        self._epoch = 0
        self._closed = False
        self.identity = {
            "backend": "CosyVoice-300M-SFT",
            "voice": "粤语女",
            "sample_rate": str(_SAMPLE_RATE),
        }

    def _owns(
        self,
        epoch: int,
        process: asyncio.subprocess.Process | None,
        generation_id: str,
    ) -> bool:
        return (
            self._epoch == epoch
            and self._process is process
            and self._active == generation_id
        )

    async def _spawn(self, epoch: int) -> asyncio.subprocess.Process:
        process = self._process
        if process is not None and process.returncode is None:
            return process
        if process is not None:
            self._process = None
        task = asyncio.create_task(
            asyncio.create_subprocess_exec(
                str(self._python),
                "-u",
                str(self._script),
                "--model",
                str(self._model),
                "--source",
                str(self._source),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=2048,
            )
        )
        self._spawn_task = task
        spawned = await asyncio.shield(task)
        if self._spawn_task is not task:
            raise _WorkerCancelled
        self._spawn_task = None
        if self._closed or self._epoch != epoch or self._active is None:
            await _terminate(spawned, self._shutdown_timeout)
            raise _WorkerCancelled
        self._process = spawned
        return spawned

    async def _timed(
        self,
        operation: Awaitable[_T],
        *,
        progress_deadline: float,
        total_deadline: float,
    ) -> _T:
        loop = asyncio.get_running_loop()
        deadline = min(progress_deadline, total_deadline)
        try:
            return await asyncio.wait_for(operation, max(0.001, deadline - loop.time()))
        except TimeoutError as error:
            if total_deadline <= progress_deadline:
                raise RuntimeError("external speech worker total timeout") from error
            raise RuntimeError("external speech worker progress timeout") from error

    async def _header(
        self,
        process: asyncio.subprocess.Process,
        *,
        progress_deadline: float,
        total_deadline: float,
    ) -> dict[str, object]:
        assert process.stdout is not None
        try:
            line = await self._timed(
                process.stdout.readuntil(b"\n"),
                progress_deadline=progress_deadline,
                total_deadline=total_deadline,
            )
        except asyncio.IncompleteReadError as error:
            raise RuntimeError("external speech worker exited unexpectedly") from error
        except asyncio.LimitOverrunError as error:
            raise RuntimeError("invalid external speech worker header") from error
        if len(line) - 1 > _HEADER_LIMIT:
            raise RuntimeError("external speech worker header exceeds limit")
        try:
            payload = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("invalid external speech worker header") from error
        if not isinstance(payload, dict):
            raise RuntimeError("invalid external speech worker header")
        return payload

    def _detach(
        self, epoch: int
    ) -> tuple[
        asyncio.subprocess.Process | None,
        asyncio.Task[asyncio.subprocess.Process] | None,
    ]:
        if self._epoch != epoch:
            return None, None
        self._epoch += 1
        self._active = None
        process, self._process = self._process, None
        spawn_task, self._spawn_task = self._spawn_task, None
        return process, spawn_task

    def _track_reaper(self, task: asyncio.Task[None]) -> None:
        self._reapers.add(task)

        def finished(completed: asyncio.Task[None]) -> None:
            self._reapers.discard(completed)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(finished)

    async def _late_spawn_reaper(
        self, spawn_task: asyncio.Task[asyncio.subprocess.Process]
    ) -> None:
        try:
            process = await asyncio.shield(spawn_task)
        except asyncio.CancelledError:
            if spawn_task.cancelled():
                return
            raise
        except Exception:
            return
        await _terminate(process, self._shutdown_timeout)

    async def _reap_resources(
        self,
        process: asyncio.subprocess.Process | None,
        spawn_task: asyncio.Task[asyncio.subprocess.Process] | None,
    ) -> None:
        spawned: asyncio.subprocess.Process | None = None
        if spawn_task is not None:
            try:
                spawned = await asyncio.wait_for(
                    asyncio.shield(spawn_task), self._shutdown_timeout
                )
            except TimeoutError:
                late_reaper = asyncio.create_task(self._late_spawn_reaper(spawn_task))
                self._track_reaper(late_reaper)
            except asyncio.CancelledError:
                if not spawn_task.cancelled():
                    raise
            except Exception:
                pass
        await _terminate(process, self._shutdown_timeout)
        if spawned is not process:
            await _terminate(spawned, self._shutdown_timeout)

    async def _stop_resources(
        self,
        process: asyncio.subprocess.Process | None,
        spawn_task: asyncio.Task[asyncio.subprocess.Process] | None,
    ) -> None:
        reaper = asyncio.create_task(self._reap_resources(process, spawn_task))
        self._track_reaper(reaper)
        await asyncio.shield(reaper)

    async def stream(self, clause: SpeechClause) -> AsyncIterator[PcmChunk]:
        async with self._serial:
            if self._closed:
                raise RuntimeError("external speech worker is closed")
            generation_id = clause.generation_id
            self._active = generation_id
            epoch = self._epoch
            process: asyncio.subprocess.Process | None = None
            complete = False
            loop = asyncio.get_running_loop()
            started = loop.time()
            first_deadline = started + self._startup_timeout
            total_deadline = started + self._total_timeout
            try:
                process = await self._timed(
                    self._spawn(epoch),
                    progress_deadline=first_deadline,
                    total_deadline=total_deadline,
                )
                if not self._owns(epoch, process, generation_id):
                    raise _WorkerCancelled
                assert process.stdin is not None
                request = (
                    json.dumps(
                        {"text": clause.text, "seed": clause.seed},
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode()
                    + b"\n"
                )
                process.stdin.write(request)
                await self._timed(
                    process.stdin.drain(),
                    progress_deadline=first_deadline,
                    total_deadline=total_deadline,
                )
                if not self._owns(epoch, process, generation_id):
                    raise _WorkerCancelled

                sequence = 0
                total_samples = 0
                first = True
                while True:
                    progress_deadline = (
                        first_deadline if first else loop.time() + self._chunk_timeout
                    )
                    header = await self._header(
                        process,
                        progress_deadline=progress_deadline,
                        total_deadline=total_deadline,
                    )
                    if not self._owns(epoch, process, generation_id):
                        raise _WorkerCancelled
                    if header == {"done": True}:
                        if total_samples == 0:
                            raise RuntimeError(
                                "external speech worker ended without PCM"
                            )
                        complete = True
                        if self._owns(epoch, process, generation_id):
                            self._active = None
                        yield PcmChunk(
                            generation_id,
                            clause.clause_id,
                            sequence,
                            _SAMPLE_RATE,
                            np.empty(0, np.float32),
                            True,
                        )
                        return
                    if set(header) != {"samples", "sample_rate"}:
                        raise RuntimeError("invalid external speech worker header")
                    samples = header["samples"]
                    sample_rate = header["sample_rate"]
                    if (
                        type(samples) is not int
                        or not 1 <= samples <= _MAX_CHUNK_SAMPLES
                    ):
                        raise RuntimeError(
                            "invalid external speech worker sample count"
                        )
                    if type(sample_rate) is not int or sample_rate != _SAMPLE_RATE:
                        raise RuntimeError("invalid external speech worker sample rate")
                    total_samples += samples
                    if total_samples > _MAX_TOTAL_SAMPLES:
                        raise RuntimeError(
                            "external speech worker exceeds sample limit"
                        )
                    assert process.stdout is not None
                    try:
                        data = await self._timed(
                            process.stdout.readexactly(samples * 4),
                            progress_deadline=progress_deadline,
                            total_deadline=total_deadline,
                        )
                    except asyncio.IncompleteReadError as error:
                        raise RuntimeError(
                            "external speech worker exited unexpectedly"
                        ) from error
                    if not self._owns(epoch, process, generation_id):
                        raise _WorkerCancelled
                    chunk = PcmChunk(
                        generation_id,
                        clause.clause_id,
                        sequence,
                        _SAMPLE_RATE,
                        np.frombuffer(data, dtype="<f4"),
                    )
                    sequence += 1
                    first = False
                    yield chunk
            except _WorkerCancelled:
                return
            except Exception:
                if not self._owns(epoch, process, generation_id):
                    return
                raise
            finally:
                if not complete:
                    owned_process, spawn_task = self._detach(epoch)
                    await self._stop_resources(owned_process, spawn_task)

    async def cancel(self, generation_id: str) -> None:
        if self._active != generation_id:
            return
        epoch = self._epoch
        process, spawn_task = self._detach(epoch)
        await self._stop_resources(process, spawn_task)

    async def close(self) -> None:
        self._closed = True
        epoch = self._epoch
        process, spawn_task = self._detach(epoch)
        await self._stop_resources(process, spawn_task)
