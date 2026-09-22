"""Owned PipeWire processes for the attended ReSpeaker bench."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from alice.contracts.speech_stream import SpeechClause
from alice.conversation.external_tts import ExternalTtsWorker
from alice.speech.tts_worker import PocketTtsWorker


def capped_pcm(pcm: NDArray[np.float32]) -> bytes:
    if pcm.ndim != 1 or not np.isfinite(pcm).all() or np.any(np.abs(pcm) > 1):
        raise ValueError("invalid output PCM")
    return (pcm * (11572 / 32768)).astype("<f4").tobytes()


def select_route(items: list[dict[str, Any]], direction: str) -> str:
    prefix = f"alsa_{direction}.usb-Seeed_Studio_ReSpeaker_Lite_0000000001-00."
    names = [
        item["name"]
        for item in items
        if str(item.get("name", "")).startswith(prefix)
        and not str(item["name"]).endswith(".monitor")
    ]
    if len(names) != 1:
        raise RuntimeError(f"expected one ReSpeaker {direction}, found {len(names)}")
    return str(names[0])


def select_pipewire_routes(items: list[dict[str, Any]]) -> tuple[str, str]:
    candidates: dict[str, list[dict[str, Any]]] = {"input": [], "output": []}
    directions = {"Audio/Source": "input", "Audio/Sink": "output"}
    for item in items:
        if item.get("type") != "PipeWire:Interface:Node":
            continue
        info = item.get("info")
        if not isinstance(info, dict):
            continue
        props = info.get("props")
        if not isinstance(props, dict):
            continue
        media_class = props.get("media.class")
        direction = (
            directions.get(media_class) if isinstance(media_class, str) else None
        )
        name = props.get("node.name")
        if direction is not None and isinstance(name, str):
            candidates[direction].append({"name": name})
    return (
        select_route(candidates["input"], "input"),
        select_route(candidates["output"], "output"),
    )


async def terminate(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), 0.5)
    except TimeoutError:
        process.kill()
        await asyncio.wait_for(process.wait(), 0.5)


async def inspect_routes() -> tuple[str, str]:
    identities = []
    for node in Path("/sys/bus/usb/devices").glob("*"):
        try:
            if (
                (node / "idVendor").read_text().strip() == "2886"
                and (node / "idProduct").read_text().strip() == "0019"
                and (node / "serial").read_text().strip() == "0000000001"
            ):
                identities.append(node.name)
        except (FileNotFoundError, NotADirectoryError):
            continue
    if len(identities) != 1:
        raise RuntimeError("ReSpeaker USB identity is missing or ambiguous")
    process = await asyncio.create_subprocess_exec(
        "pw-dump",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 3)
        if process.returncode != 0 or len(output) > 1_000_000:
            raise RuntimeError("cannot inspect Linux audio routes")
        payload = json.loads(output)
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            raise RuntimeError("invalid PipeWire route inventory")
        return select_pipewire_routes(payload)
    finally:
        await terminate(process)


async def buffered_frames(
    source: AsyncIterator[NDArray[np.float32]],
    *,
    max_frames: int = 4,
    max_age_s: float = 0.25,
) -> AsyncGenerator[NDArray[np.float32], None]:
    if max_frames < 1 or max_age_s <= 0:
        raise ValueError("capture queue bounds must be positive")
    queue: asyncio.Queue[tuple[float, NDArray[np.float32]]] = asyncio.Queue(
        maxsize=max_frames
    )
    changed = asyncio.Event()
    complete = False
    fault: BaseException | None = None

    async def read() -> None:
        nonlocal complete, fault
        try:
            async for frame in source:
                try:
                    queue.put_nowait((time.monotonic(), frame))
                except asyncio.QueueFull:
                    fault = RuntimeError("microphone capture queue overflow")
                    return
                finally:
                    changed.set()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            fault = error
        finally:
            close = getattr(source, "aclose", None)
            if close is not None:
                try:
                    await close()
                except BaseException as error:
                    if fault is None:
                        fault = error
            complete = True
            changed.set()

    reader = asyncio.create_task(read())
    try:
        while True:
            if fault is not None:
                raise fault
            if not queue.empty():
                captured_at, frame = queue.get_nowait()
                if fault is not None:
                    raise fault
                if time.monotonic() - captured_at > max_age_s:
                    raise RuntimeError("microphone capture frame is stale")
                yield frame
                continue
            if complete:
                return
            changed.clear()
            if fault is not None or complete or not queue.empty():
                continue
            await changed.wait()
    finally:
        reader.cancel()
        done, _ = await asyncio.wait({reader}, timeout=1)
        if not done:
            raise RuntimeError("microphone capture reader did not stop")
        await asyncio.gather(reader, return_exceptions=True)


async def capture(source: str) -> AsyncGenerator[NDArray[np.float32], None]:
    raw_stream = _pipe_frames(source)
    frame_stream = buffered_frames(raw_stream)
    try:
        async for frame in frame_stream:
            yield frame
    finally:
        async with asyncio.timeout(2):
            await frame_stream.aclose()


async def _pipe_frames(source: str) -> AsyncGenerator[NDArray[np.float32], None]:
    process = await asyncio.create_subprocess_exec(
        "pw-cat",
        "--record",
        "--raw",
        "--target",
        source,
        "--rate",
        "16000",
        "--channels",
        "2",
        "--format",
        "s16",
        "--latency",
        "32ms",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=4096,
    )
    try:
        assert process.stdout is not None
        while True:
            try:
                data = await asyncio.wait_for(process.stdout.readexactly(2048), 2)
            except (asyncio.IncompleteReadError, TimeoutError) as error:
                raise RuntimeError("microphone disconnected or stalled") from error
            yield (
                np.frombuffer(data, "<i2").reshape(-1, 2)[:, 0].astype(np.float32)
                * np.float32(1 / 32768)
            )
    finally:
        await terminate(process)


class Speaker:
    def __init__(
        self,
        emit: Callable[..., Any],
        *,
        enabled: bool = False,
        cantonese_model: Path | None = None,
        cantonese_source: Path | None = None,
        cantonese_python: Path | None = None,
    ) -> None:
        self.emit, self.enabled = emit, enabled
        self.worker = PocketTtsWorker(offline=True)
        self.cantonese_worker: ExternalTtsWorker | None = None
        if cantonese_model is not None:
            if cantonese_source is None or cantonese_python is None:
                raise ValueError("Cantonese source and Python environment are required")
            self.cantonese_worker = ExternalTtsWorker(
                cantonese_python,
                Path(__file__).with_name("cosy_worker.py"),
                cantonese_model,
                cantonese_source,
            )
        self._active_language = "English"
        self.process: asyncio.subprocess.Process | None = None
        self.active: str | None = None
        self.sink: str | None = None
        self.busy = False
        self.guard_until = 0.0
        self._epoch = 0

    @staticmethod
    def clause(text: str, generation: str) -> SpeechClause:
        return SpeechClause(
            generation_id=generation,
            clause_id=uuid.uuid4().hex,
            sequence=0,
            text=text,
            vector=(0.0, 0.0, 0.0),
            intensity=0.0,
            seed=7,
            end_of_response=True,
        )

    async def warm(self) -> None:
        epoch = self._epoch
        active = "warm-" + uuid.uuid4().hex
        self.active = active
        try:
            voices: list[tuple[str, PocketTtsWorker | ExternalTtsWorker, str]] = [
                ("English", self.worker, "Ready.")
            ]
            if self.cantonese_worker is not None:
                voices.append(("Cantonese", self.cantonese_worker, "你好呀。"))
            for language, worker, text in voices:
                self._active_language = language
                async for _ in worker.stream(self.clause(text, active)):
                    if epoch != self._epoch:
                        return
                if epoch != self._epoch:
                    return
        finally:
            if epoch == self._epoch and self.active == active:
                self.active = None
        if epoch == self._epoch:
            self.emit(
                "tts",
                "ready",
                message="English and Cantonese voices warm"
                if self.cantonese_worker
                else "Azelma model warm",
            )

    async def speak(
        self, text: str, generation: str, *, audible: bool, language: str = "English"
    ) -> None:
        if language not in {"English", "Cantonese"}:
            raise ValueError("unsupported spoken language")
        worker = self.worker if language == "English" else self.cantonese_worker
        if worker is None:
            raise RuntimeError("Cantonese voice is not configured")
        epoch = self._epoch
        self.active = generation
        self._active_language = language
        total, first = 0, True
        start = time.monotonic()
        voice = "Azelma" if language == "English" else "CosyVoice 粤语女"
        self.emit(
            "tts",
            "generating",
            text=text,
            generation_id=generation,
            language=language,
            voice=voice,
        )
        try:
            async for chunk in worker.stream(self.clause(text, generation)):
                if epoch != self._epoch:
                    return
                if not len(chunk.pcm):
                    continue
                if chunk.sample_rate != 24000:
                    raise RuntimeError("unexpected TTS sample rate")
                total += len(chunk.pcm)
                if total > 24000 * 15:
                    raise RuntimeError("spoken clause exceeds 15-second limit")
                if first:
                    self.emit(
                        "tts",
                        "streaming",
                        latency_ms=(time.monotonic() - start) * 1000,
                        generation_id=generation,
                        language=language,
                        voice=voice,
                    )
                    if self.enabled and audible:
                        if not self.sink:
                            raise RuntimeError("no verified speaker route")
                        spawned_process = await asyncio.create_subprocess_exec(
                            "pw-cat",
                            "--playback",
                            "--raw",
                            "--target",
                            self.sink,
                            "--rate",
                            "24000",
                            "--channels",
                            "1",
                            "--format",
                            "f32",
                            "--latency",
                            "32ms",
                            "-",
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        if epoch != self._epoch:
                            await terminate(spawned_process)
                            return
                        self.process = spawned_process
                        self.busy = True
                    self.emit(
                        "output",
                        "submitting" if self.process else "dry",
                        message="Software submission; acoustic start unmeasured",
                        generation_id=generation,
                    )
                    first = False
                if self.process:
                    assert self.process.stdin is not None
                    self.process.stdin.write(capped_pcm(chunk.pcm))
                    await asyncio.wait_for(self.process.stdin.drain(), 2)
            if epoch != self._epoch:
                return
            drain_process = self.process
            if drain_process:
                assert drain_process.stdin is not None
                drain_process.stdin.close()
                await asyncio.wait_for(drain_process.wait(), 8)
                if epoch != self._epoch or self.process is not drain_process:
                    return
                if drain_process.returncode != 0:
                    raise RuntimeError("speaker process failed")
            self.emit(
                "output",
                "drained" if drain_process else "dry_complete",
                samples=total,
                generation_id=generation,
                message="Native player drained" if drain_process else "No sound played",
            )
        finally:
            if epoch == self._epoch:
                cleanup_process = self.process
                was_busy = self.busy
                await terminate(cleanup_process)
                if epoch == self._epoch and self.process is cleanup_process:
                    self.process = None
                    self.guard_until = time.monotonic() + 0.2 if was_busy else 0
                    self.busy = False
                    if self.active == generation:
                        self.active = None

    async def stop(self) -> None:
        self._epoch += 1
        epoch = self._epoch
        process, self.process = self.process, None
        active, self.active = self.active, None
        worker = (
            self.worker if self._active_language == "English" else self.cantonese_worker
        )
        start = time.monotonic()
        await terminate(process)
        if active and worker is not None:
            await worker.cancel(active)
        if epoch != self._epoch:
            return
        self.busy = False
        self.guard_until = time.monotonic() + 0.2
        self.emit(
            "output",
            "stopped",
            latency_ms=(time.monotonic() - start) * 1000,
            message="Software stop; acoustic timing unmeasured",
        )

    async def close(self) -> None:
        await self.stop()
        await self.worker.close()
        if self.cantonese_worker is not None:
            await self.cantonese_worker.close()
