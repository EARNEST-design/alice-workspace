"""Incremental speech experiment with local artifacts and no actuator authority."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import shutil
import subprocess
import time
import wave
from collections.abc import AsyncIterator
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import numpy as np

from alice.contracts.speech_stream import SpeechClause, jsonl_clauses
from alice.speech.composer import compose_frame
from alice.speech.expression_bridge import ExpressionBridge
from alice.speech.stream_playback import SimulatedPlayback, SoundDevicePlayback
from alice.speech.stream_session import SpeechStreamSession
from alice.speech.timeline import SpeechFrame
from alice.speech.tts_worker import PcmChunk, PocketTtsWorker


class _RecordingWorker:
    def __init__(self, worker: PocketTtsWorker, output: Path) -> None:
        self.worker, self.output = worker, output
        self.wav: wave.Wave_write | None = None
        self.sample_rate = 0
        self.clauses: list[SpeechClause] = []
        self.chunks: list[dict[str, object]] = []
        self.samples = 0

    @property
    def identity(self) -> dict[str, str]:
        return self.worker.identity

    async def stream(self, clause: SpeechClause) -> AsyncIterator[PcmChunk]:
        self.clauses.append(clause)
        async for chunk in self.worker.stream(clause):
            if self.wav is None:
                self.wav = wave.open(str(self.output / "generated.wav"), "wb")
                self.wav.setnchannels(1)
                self.wav.setsampwidth(2)
                self.wav.setframerate(chunk.sample_rate)
                self.sample_rate = chunk.sample_rate
            raw = np.rint(chunk.pcm * 32767).astype("<i2").tobytes()
            self.wav.writeframesraw(raw)
            self.chunks.append(
                {
                    "clause_id": clause.clause_id,
                    "sequence": chunk.sequence,
                    "start_sample": self.samples,
                    "sample_count": len(chunk.pcm),
                    "received_monotonic_s": time.monotonic(),
                    "final": chunk.final,
                    "pcm_float32_sha256": hashlib.sha256(
                        chunk.pcm.tobytes()
                    ).hexdigest(),
                }
            )
            self.samples += len(chunk.pcm)
            yield chunk

    async def cancel(self, generation_id: str) -> None:
        await self.worker.cancel(generation_id)

    def close_recording(self, tail_samples: int) -> None:
        if self.wav is not None:
            if tail_samples > 0:
                self.wav.writeframesraw(bytes(tail_samples * 2))
            self.wav.close()


def _derivatives(rows: list[dict[str, Any]]) -> dict[str, object]:
    # A fault release occurs at the last audible sample, as a separate event.
    # Its instantaneous ownership change has no finite time derivative; retain
    # it in composed.jsonl but exclude it from motion-cadence measurements.
    rows = [
        row
        for index, row in enumerate(rows)
        if index == 0
        or row["proposal"]["offset_s"] != rows[index - 1]["proposal"]["offset_s"]
    ]
    if len(rows) < 3:
        return {}
    times = np.array([r["proposal"]["offset_s"] for r in rows])
    intervals = np.diff(times)
    if np.any(intervals <= 0):
        raise ValueError("composed trace has nonmonotonic sample times")
    result: dict[str, object] = {}
    for index, target in enumerate(rows[0]["proposal"]["targets"]):
        positions = np.array(
            [row["proposal"]["targets"][index]["normalized_position"] for row in rows]
        )
        delta = np.diff(positions)
        velocity = delta / intervals
        acceleration = np.diff(velocity) / ((intervals[1:] + intervals[:-1]) / 2)
        result[target["actuator_name"]] = {
            "min": float(positions.min()),
            "max": float(positions.max()),
            "max_step": float(np.max(np.abs(delta))),
            "max_rate_per_s": float(np.max(np.abs(velocity))),
            "max_acceleration_per_s2": float(np.max(np.abs(acceleration))),
        }
    return result


def _preview(
    output: Path,
    recorder: _RecordingWorker,
    rows: list[dict[str, Any]],
    bridge: ExpressionBridge,
) -> None:
    if not rows or not (output / "generated.wav").exists():
        return
    starts = [
        (r["clause_id"], r["start_sample"])
        for r in recorder.chunks
        if r["sequence"] == 0
    ]
    spans = [
        {
            "segment_index": i,
            "start_sample": start,
            "end_sample": starts[i + 1][1] if i + 1 < len(starts) else recorder.samples,
        }
        for i, (_, start) in enumerate(starts)
    ]
    data = {
        "plan": {"segments": [c.model_dump() for c in recorder.clauses]},
        "expression_mode": str(bridge.identity["source"]),
        "timeline": {
            "sample_rate": recorder.sample_rate,
            "sample_count": rows[-1]["frame"]["sample_index"],
            "spans": spans,
            "frames": [r["frame"] for r in rows],
            "motion": [r["proposal"] for r in rows],
        },
    }
    template = (
        files("alice.resources").joinpath("speech-stream-preview.html").read_text()
    )
    html = template.replace(
        "__SPEECH_DATA__",
        json.dumps(data).replace("<", "\\u003c").replace("&", "\\u0026"),
    )
    html = html.replace(
        "__AUDIO_BASE64__",
        base64.b64encode((output / "generated.wav").read_bytes()).decode(),
    )
    (output / "preview.html").write_text(html)


def run_stream(
    clauses_path: Path,
    output: Path,
    *,
    config_root: Path,
    mode: Literal["authored", "learned-fallback"],
    play: bool,
) -> int:
    """Keep source/model/proposal/PCM evidence even if a generation fails."""
    if output.exists():
        raise ValueError("output directory already exists; choose a new run path")

    async def run() -> dict[str, object]:
        source = jsonl_clauses(clauses_path)
        first = await anext(source, None)
        if first is None:
            raise ValueError("empty clause fixture")
        # Construct inference before opening the speaker. Neither opens serial.
        bridge = ExpressionBridge(
            config_root=config_root,
            seed=first.seed,
            generation_id=first.generation_id,
            mode=mode,
        )
        output.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(clauses_path, output / "source.jsonl")
        shutil.copyfile(
            config_root / "speech/sync-hardware-v1.json", output / "sync-config.json"
        )
        shutil.copyfile(
            config_root / "speech/jaw-speech-lead-v1.json", output / "jaw-baseline.json"
        )
        worker = PocketTtsWorker(offline=True)
        recorder = _RecordingWorker(worker, output)
        rows: list[dict[str, Any]] = []

        async def committed() -> AsyncIterator[SpeechClause]:
            yield first
            async for clause in source:
                yield clause

        def emit(frame: SpeechFrame) -> None:
            expression = bridge.advance(
                frame, frame.sample_index, recorder.sample_rate or 24000
            )
            rows.append(
                {
                    "frame": frame.model_dump(),
                    "proposal": compose_frame(
                        expression, frame, bridge.sync_config
                    ).model_dump(),
                    "observed_monotonic_s": time.monotonic(),
                }
            )

        session = SpeechStreamSession(
            worker=recorder,
            emit=emit,
            config=bridge.sync_config,
            playback=SoundDevicePlayback() if play else SimulatedPlayback(),
        )
        try:
            return await session.run(committed(), asyncio.Event())
        finally:
            await source.aclose()
            await worker.close()
            samples = (
                session.timeline.generated_samples
                if session.timeline
                else recorder.samples
            )
            recorder.close_recording(max(0, samples - recorder.samples))
            (output / "chunks.json").write_text(
                json.dumps(recorder.chunks, indent=2) + "\n"
            )
            (output / "composed.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows)
            )
            (output / "expression-state.json").write_text(bridge.snapshot())
            (output / "metrics.json").write_text(
                json.dumps(session.metrics, indent=2) + "\n"
            )
            derivatives = _derivatives(rows)
            (output / "proposal-derivatives.json").write_text(
                json.dumps(derivatives, indent=2) + "\n"
            )
            _preview(output, recorder, rows, bridge)
            root = Path(__file__).resolve().parents[3]
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            code_hashes = {
                str(path.relative_to(root)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted((root / "src/alice").rglob("*.py"))
            }
            manifest = {
                "schema_version": "speech-stream-run/v1",
                "actuation_mode": "none",
                "voice": "azelma",
                "expression": bridge.identity,
                "metrics": session.metrics,
                "code_revision": revision,
                "code_hashes": code_hashes,
                "input_sha256": hashlib.sha256(clauses_path.read_bytes()).hexdigest(),
                "source": "Authored text and generated audio; no participant data.",
                "conclusion": (
                    "Incremental software integration; generated PCM and proposed "
                    "targets are not physical motion evidence."
                ),
                "jaw_comparison": {
                    "mouth_lead_s": 0.1,
                    "profile": "jaw-baseline.json",
                    "raw_proposal_derivatives": derivatives.get("mouth_open"),
                    "requires_trusted_executor": True,
                },
                "artifacts": {
                    str(path.relative_to(output)): hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                    for path in sorted(output.iterdir())
                    if path.is_file()
                },
            }
            (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    metrics = asyncio.run(run())
    print(
        json.dumps(
            {"output": str(output.resolve()), **metrics, "actuation_mode": "none"}
        )
    )
    return 0 if metrics["outcome"] == "completed" else 130
