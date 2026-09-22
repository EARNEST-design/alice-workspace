"""Real-process protocol tests for the isolated Cantonese TTS worker."""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from alice.contracts.speech_stream import SpeechClause
from alice.conversation.external_tts import ExternalTtsWorker
from alice.speech.tts_worker import PcmChunk

STUB = r"""
import argparse
import json
import math
import os
import signal
import struct
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--model", required=True)
parser.add_argument("--source", required=True)
args = parser.parse_args()
with open(args.source, "a", encoding="utf-8") as marker:
    marker.write(f"spawn:{os.getpid()}\n")
    marker.flush()

def emit(values):
    print(json.dumps({"samples": len(values), "sample_rate": 24000}), flush=True)
    sys.stdout.buffer.write(struct.pack(f"<{len(values)}f", *values))
    sys.stdout.buffer.flush()

for line in sys.stdin:
    request = json.loads(line)
    mode = request["text"]
    with open(args.source, "a", encoding="utf-8") as marker:
        marker.write(f"request:{os.getpid()}:{mode}\n")
        marker.flush()
    if mode == "stall-first":
        time.sleep(2)
    elif mode == "ignore-term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        with open(args.source, "a", encoding="utf-8") as marker:
            marker.write(f"armed:{os.getpid()}\n")
            marker.flush()
        time.sleep(2)
    elif mode == "stall-later":
        emit([0.25, -0.25])
        time.sleep(2)
    elif mode == "slow-forever":
        for _ in range(20):
            time.sleep(0.03)
            emit([0.125])
    elif mode == "oversize":
        print(json.dumps({"samples": 24001, "sample_rate": 24000}), flush=True)
    elif mode == "nonfinite":
        emit([math.nan])
    elif mode == "empty-done":
        print(json.dumps({"done": True}), flush=True)
    elif mode == "bad-header":
        print("{" + "x" * 1100, flush=True)
    elif mode == "short-pcm":
        print(json.dumps({"samples": 2, "sample_rate": 24000}), flush=True)
        sys.stdout.buffer.write(struct.pack("<f", 0.25))
        sys.stdout.buffer.flush()
        raise SystemExit(8)
    elif mode == "too-long":
        for _ in range(16):
            emit([0.0] * 24000)
    elif mode == "exit":
        raise SystemExit(9)
    else:
        emit([0.25, -0.25, 0.5])
        print(json.dumps({"done": True}), flush=True)
"""


def clause(text: str, generation: str = "generation-1") -> SpeechClause:
    return SpeechClause(
        generation_id=generation,
        clause_id=f"clause-{generation}",
        sequence=0,
        text=text,
        vector=(0.0, 0.0, 0.0),
        intensity=0.0,
        seed=17,
        end_of_response=True,
    )


def files(tmp_path: Path) -> tuple[Path, Path, Path]:
    script = tmp_path / "stub_worker.py"
    script.write_text(textwrap.dedent(STUB))
    model = tmp_path / "model"
    model.mkdir()
    marker = tmp_path / "source-marker.txt"
    marker.touch()
    return script, model, marker


async def collect(worker: ExternalTtsWorker, item: SpeechClause) -> list[PcmChunk]:
    chunks: list[PcmChunk] = []
    async for chunk in worker.stream(item):
        chunks.append(chunk)
    return chunks


def marker_pids(marker: Path) -> list[int]:
    return [
        int(line.split(":", 1)[1])
        for line in marker.read_text().splitlines()
        if line.startswith("spawn:")
    ]


def test_worker_reuses_one_process_and_yields_validated_pcm(tmp_path: Path) -> None:
    async def run() -> None:
        script, model, marker = files(tmp_path)
        worker = ExternalTtsWorker(
            Path(sys.executable), script, model, marker, startup_timeout_s=1
        )
        first = await collect(worker, clause("normal", "one"))
        second = await collect(worker, clause("normal", "two"))

        assert marker_pids(marker) == [marker_pids(marker)[0]]
        for chunks, generation in ((first, "one"), (second, "two")):
            assert len(chunks) == 2
            pcm, final = chunks
            assert pcm.generation_id == generation
            assert pcm.clause_id == f"clause-{generation}"
            assert pcm.sequence == 0
            assert pcm.sample_rate == 24_000
            np.testing.assert_array_equal(
                pcm.pcm, np.array([0.25, -0.25, 0.5], np.float32)
            )
            assert pcm.final is False
            assert final.sequence == 1
            assert final.final is True
            assert len(final.pcm) == 0
        assert worker.identity == {
            "backend": "CosyVoice-300M-SFT",
            "voice": "粤语女",
            "sample_rate": "24000",
        }

        pid = marker_pids(marker)[0]
        await worker.close()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("oversize", "sample count"),
        ("nonfinite", "finite mono PCM"),
        ("empty-done", "without PCM"),
        ("bad-header", "header"),
        ("short-pcm", "exited"),
        ("too-long", "sample limit"),
        ("exit", "exited"),
    ],
)
def test_protocol_fault_terminates_worker_and_next_request_restarts(
    tmp_path: Path, mode: str, message: str
) -> None:
    async def run() -> None:
        script, model, marker = files(tmp_path)
        worker = ExternalTtsWorker(
            Path(sys.executable), script, model, marker, startup_timeout_s=1
        )
        with pytest.raises((RuntimeError, ValueError), match=message):
            await collect(worker, clause(mode, "bad"))

        chunks = await collect(worker, clause("normal", "restarted"))
        assert chunks[-1].final is True
        assert len(marker_pids(marker)) == 2
        assert len(set(marker_pids(marker))) == 2
        await worker.close()

    asyncio.run(run())


def test_first_and_later_chunk_stalls_have_separate_deadlines(tmp_path: Path) -> None:
    async def run() -> None:
        script, model, marker = files(tmp_path)
        worker = ExternalTtsWorker(
            Path(sys.executable),
            script,
            model,
            marker,
            startup_timeout_s=0.1,
            chunk_timeout_s=0.05,
        )
        with pytest.raises(RuntimeError, match="progress timeout"):
            await collect(worker, clause("stall-first", "first"))

        stream = worker.stream(clause("stall-later", "later"))
        first = await anext(stream)
        assert len(first.pcm) == 2
        with pytest.raises(RuntimeError, match="progress timeout"):
            await anext(stream)
        await worker.close()

    asyncio.run(run())


def test_total_wall_deadline_bounds_many_small_progress_chunks(tmp_path: Path) -> None:
    async def run() -> None:
        script, model, marker = files(tmp_path)
        worker = ExternalTtsWorker(
            Path(sys.executable),
            script,
            model,
            marker,
            startup_timeout_s=0.1,
            chunk_timeout_s=0.1,
            total_timeout_s=0.08,
        )
        with pytest.raises(RuntimeError, match="total timeout"):
            await collect(worker, clause("slow-forever", "bounded"))
        await worker.close()

    asyncio.run(run())


def test_cancel_only_kills_the_matching_generation_and_worker_restarts(
    tmp_path: Path,
) -> None:
    async def wait_for_request(marker: Path, mode: str) -> None:
        for _ in range(100):
            if f":{mode}" in marker.read_text():
                return
            await asyncio.sleep(0.005)
        raise AssertionError("stub did not receive request")

    async def run() -> None:
        script, model, marker = files(tmp_path)
        worker = ExternalTtsWorker(
            Path(sys.executable), script, model, marker, startup_timeout_s=3
        )
        consuming = asyncio.create_task(
            collect(worker, clause("stall-first", "active"))
        )
        await wait_for_request(marker, "stall-first")
        first_pid = marker_pids(marker)[0]

        await worker.cancel("other-generation")
        assert not consuming.done()
        os.kill(first_pid, 0)

        await worker.cancel("active")
        assert await asyncio.wait_for(consuming, timeout=1) == []
        with pytest.raises(ProcessLookupError):
            os.kill(first_pid, 0)

        chunks = await collect(worker, clause("normal", "new"))
        assert chunks[-1].final is True
        assert marker_pids(marker)[-1] != first_pid
        await worker.close()

    asyncio.run(run())


def test_cancel_escalates_to_kill_within_configured_bounds(tmp_path: Path) -> None:
    async def run() -> None:
        script, model, marker = files(tmp_path)
        worker = ExternalTtsWorker(
            Path(sys.executable),
            script,
            model,
            marker,
            startup_timeout_s=3,
            shutdown_timeout_s=0.05,
        )
        consuming = asyncio.create_task(
            collect(worker, clause("ignore-term", "active"))
        )
        for _ in range(100):
            if "armed:" in marker.read_text():
                break
            await asyncio.sleep(0.005)
        else:
            raise AssertionError("stub did not arm SIGTERM handler")
        pid = marker_pids(marker)[0]

        started = asyncio.get_running_loop().time()
        await worker.cancel("active")
        elapsed = asyncio.get_running_loop().time() - started

        assert elapsed < 0.5
        assert await asyncio.wait_for(consuming, timeout=1) == []
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        await worker.close()

    asyncio.run(run())


def test_cancelled_spawn_reaps_process_that_completed_with_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = None
            self.returncode: int | None = None
            self.terminated = False

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def kill(self) -> None:
            self.terminated = True
            self.returncode = -9

        async def wait(self) -> int:
            assert self.returncode is not None
            return self.returncode

    async def run() -> None:
        process = FakeProcess()
        spawning = asyncio.Event()
        release_spawn = asyncio.Event()
        consumer: asyncio.Task[list[PcmChunk]] | None = None

        async def spawn(*_args: object, **_kwargs: object) -> FakeProcess:
            awaitable_consumer = consumer
            assert awaitable_consumer is not None
            spawning.set()
            await release_spawn.wait()
            awaitable_consumer.cancel()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        script, model, marker = files(tmp_path)
        worker = ExternalTtsWorker(
            Path(sys.executable),
            script,
            model,
            marker,
            shutdown_timeout_s=0.05,
        )
        consumer = asyncio.create_task(collect(worker, clause("normal")))
        await spawning.wait()

        release_spawn.set()
        with pytest.raises(asyncio.CancelledError):
            await consumer

        assert process.terminated is True
        await worker.close()

    asyncio.run(run())


def test_cancelled_cleanup_keeps_a_durable_late_spawn_reaper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = None
            self.returncode: int | None = None
            self.terminated = False

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def kill(self) -> None:
            self.terminated = True
            self.returncode = -9

        async def wait(self) -> int:
            assert self.returncode is not None
            return self.returncode

    async def run() -> None:
        process = FakeProcess()
        spawning = asyncio.Event()
        release_spawn = asyncio.Event()

        async def spawn(*_args: object, **_kwargs: object) -> FakeProcess:
            spawning.set()
            await release_spawn.wait()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        script, model, marker = files(tmp_path)
        worker = ExternalTtsWorker(
            Path(sys.executable),
            script,
            model,
            marker,
            shutdown_timeout_s=0.05,
        )
        consumer = asyncio.create_task(collect(worker, clause("normal")))
        await spawning.wait()

        cleanup = asyncio.create_task(worker.cancel("generation-1"))
        await asyncio.sleep(0)
        cleanup.cancel()
        cleanup_cancelled = False
        try:
            await cleanup
        except asyncio.CancelledError:
            cleanup_cancelled = True

        await asyncio.sleep(0.08)
        release_spawn.set()
        assert await asyncio.wait_for(consumer, timeout=1) == []
        for _ in range(100):
            if process.terminated:
                break
            await asyncio.sleep(0.005)

        assert cleanup_cancelled is True
        assert process.terminated is True
        await worker.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"startup_timeout_s": 0},
        {"startup_timeout_s": 91},
        {"chunk_timeout_s": 11},
        {"shutdown_timeout_s": 0.6},
        {"total_timeout_s": float("inf")},
    ],
)
def test_timeout_configuration_is_finite_and_bounded(
    tmp_path: Path, kwargs: dict[str, float]
) -> None:
    script, model, marker = files(tmp_path)
    with pytest.raises(ValueError, match="timeout"):
        ExternalTtsWorker(Path(sys.executable), script, model, marker, **kwargs)
