"""Incremental speech with guarded selected facial servos; simulated by default."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from threading import Event
from typing import Any

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.contracts.speech_stream import ClauseSequence, SpeechClause
from alice.experiments.jaw_trial_cli import _check_owners as _check_owners
from alice.experiments.jaw_trial_cli import _write
from alice.hardware.face_scope import FACE_CHANNELS, face_manifest, face_profiles
from alice.hardware.manifest import HardwareManifest, load_manifest
from alice.speech.composer import compose_frame
from alice.speech.expression_bridge import ExpressionBridge
from alice.speech.face_runtime import FaceRuntime
from alice.speech.face_stream import FaceCommandStream
from alice.speech.stream_cli import _derivatives, _preview, _RecordingWorker
from alice.speech.stream_playback import SimulatedPlayback, SoundDevicePlayback
from alice.speech.stream_session import SpeechStreamSession
from alice.speech.timeline import SpeechFrame
from alice.speech.tts_worker import PocketTtsWorker

ROOT = Path(__file__).resolve().parents[3]


def _factory(
    full: HardwareManifest,
    generation: str,
    hardware: bool,
    report: dict[str, Any],
    output: Path,
) -> Callable[[Event], FaceCommandStream]:
    from alice.speech.face_adapter import face_factory

    return face_factory(
        full, generation, hardware, report, output, config_root=ROOT / "config"
    )


async def _run(
    clauses: list[SpeechClause],
    output: Path,
    *,
    hardware: bool,
    play: bool,
    report: dict[str, Any],
    sad_hold_s: float = 0,
) -> None:
    first = clauses[0]
    bridge = ExpressionBridge(
        config_root=output / "config",
        seed=first.seed,
        generation_id=first.generation_id,
        mode="authored",
    )
    full = load_manifest(ROOT / "hardware/alice-face-v1.yaml")
    _write(output / "source-hardware.json", full.model_dump(mode="json"))
    _write(output / "active-hardware.json", face_manifest(full).model_dump(mode="json"))
    _write(
        output / "channel-profiles.json",
        {n: c.model_dump(mode="json") for n, c in face_profiles().items()},
    )
    worker = PocketTtsWorker(offline=True)
    recorder = _RecordingWorker(worker, output)
    rows: list[dict[str, Any]] = []
    cancel = asyncio.Event()
    loop = asyncio.get_running_loop()
    session: SpeechStreamSession | None = None

    def stop_audio() -> None:
        if session is not None and session.ring is not None:
            session.ring.abort()
        loop.call_soon_threadsafe(cancel.set)

    runtime = FaceRuntime(
        _factory(full, first.generation_id, hardware, report, output),
        on_fault=stop_audio,
    )

    def stop_trial() -> None:
        stop_audio()
        runtime.abort("audio/session cancelled")

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_trial)

    def emit(frame: SpeechFrame) -> None:
        source_time = time.monotonic_ns()
        # Terminal ownership release is evidence, never permission for Home.
        if frame.speech_weight == 0:
            rows.append(
                {
                    "frame": frame.model_dump(),
                    "terminal_release": True,
                    "observed_monotonic_s": time.monotonic(),
                }
            )
            return
        if frame.sample_index / (recorder.sample_rate or 24000) > 10:
            raise RuntimeError("face trial exceeded ten seconds of audio")
        expression = bridge.advance(
            frame, frame.sample_index, recorder.sample_rate or 24000
        )
        composed = compose_frame(expression, frame, bridge.sync_config)
        runtime.offer(
            composed,
            first.generation_id,
            frame.sample_index,
            source_monotonic_ns=source_time,
        )
        rows.append(
            {
                "frame": frame.model_dump(),
                "proposal": composed.model_dump(),
                "observed_monotonic_s": time.monotonic(),
            }
        )

    async def source() -> AsyncIterator[SpeechClause]:
        for clause in clauses:
            yield clause

    session = SpeechStreamSession(
        worker=recorder,
        emit=emit,
        config=bridge.sync_config,
        playback=SoundDevicePlayback(fault_signal=runtime.cancel_signal)
        if play or hardware
        else SimulatedPlayback(),
        on_abort=lambda: runtime.abort("audio pipeline aborted"),
    )
    completed = False
    runtime.start()
    try:
        if not await asyncio.to_thread(runtime.ready.wait, 5):
            raise RuntimeError("face startup deadline exceeded")
        runtime.raise_if_failed()
        report["outcome"] = "running"
        _write(output / "manifest.json", report)
        await session.run(source(), cancel)
        runtime.raise_if_failed()
        if session.metrics["outcome"] != "completed":
            raise RuntimeError(f"audio {session.metrics['outcome']}")
        hold_pose = None
        if sad_hold_s:
            hold_pose = TargetUpdate(
                offset_s=0,
                targets=(
                    ActuatorTarget(actuator_name="mouth_open", normalized_position=-1),
                    ActuatorTarget(
                        actuator_name="left_mouth_corner", normalized_position=-0.8
                    ),
                    ActuatorTarget(
                        actuator_name="right_mouth_corner", normalized_position=0.8
                    ),
                ),
            )
        report["post_speech_pose"] = (
            hold_pose.model_dump(mode="json") if hold_pose else None
        )
        report["post_speech_hold_s"] = sad_hold_s
        runtime.complete(hold_pose=hold_pose, hold_s=sad_hold_s)
        if not await asyncio.to_thread(runtime.done.wait, 14):
            raise RuntimeError("face completion deadline exceeded")
        runtime.raise_if_failed()
        completed = True
        report["outcome"] = "completed"
    finally:
        primary = sys.exception()
        cleanup_errors: list[str] = []
        if not completed:
            runtime.abort("speech did not complete successfully")
        try:
            await asyncio.to_thread(runtime.join)
        except BaseException as exc:
            cleanup_errors.append(f"face runtime join: {exc}")
        try:
            await worker.close()
        except BaseException as exc:
            cleanup_errors.append(f"worker close: {exc}")
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        samples = (
            session.timeline.generated_samples if session.timeline else recorder.samples
        )
        recorder.close_recording(max(0, samples - recorder.samples))
        _write(output / "chunks.json", recorder.chunks)
        _write(output / "audio-metrics.json", session.metrics)
        (output / "composed.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows)
        )
        motion = [r for r in rows if "proposal" in r]
        _write(output / "proposal-derivatives.json", _derivatives(motion))
        _preview(output, recorder, motion, bridge)
        (output / "expression-state.json").write_text(bridge.snapshot())
        stream = runtime.stream
        (output / "commands.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in stream.records) if stream else ""
        )
        report.update(
            audio=session.metrics,
            expression=bridge.identity,
            controller_home_confirmed=bool(stream and stream.home_confirmed),
            consumed_source_revisions=len(stream.consumed_revisions) if stream else 0,
            physical_sync_measured=False,
            operator_feedback=None,
        )
        report["cleanup_errors"] = cleanup_errors
        if cleanup_errors:
            if primary is not None:
                primary.add_note("; ".join(cleanup_errors))
            else:
                raise RuntimeError("; ".join(cleanup_errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clauses", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config-root", type=Path, default=ROOT / "config")
    parser.add_argument(
        "--sad-hold-s",
        type=float,
        default=0,
        help="Hold a closed-mouth frown after the final negative clause (0–2 seconds)",
    )
    parser.add_argument(
        "--play", action="store_true", help="Real speakers, simulated face"
    )
    parser.add_argument(
        "--enable-hardware",
        action="store_true",
        help="Explicit attended selected-face run",
    )
    args = parser.parse_args(argv)
    output: Path | None = None
    report: dict[str, Any] = {
        "schema_version": "face-speech-run/v1",
        "outcome": "preparing",
        "actuation_mode": "hardware" if args.enable_hardware else "simulated",
        "selected_channels": FACE_CHANNELS,
        "electrical_margin_verified": False,
        "source": "Authored text/expression and generated audio; no participant data.",
    }
    try:
        raw_source = args.clauses.read_bytes()
        clauses = [
            SpeechClause.model_validate_json(line)
            for line in raw_source.splitlines()
            if line.strip()
        ]
        ledger = ClauseSequence()
        for clause in clauses:
            ledger.commit(clause)
        ledger.finish()
        if not 0 <= args.sad_hold_s <= 2:
            raise ValueError("sad hold must be between zero and two seconds")
        if args.sad_hold_s and clauses[-1].vector[0] >= -0.2:
            raise ValueError("sad hold requires a final authored negative clause")
        args.output.mkdir(parents=True, exist_ok=False)
        output = args.output
        (output / "source.jsonl").write_bytes(raw_source)
        shutil.copytree(args.config_root, output / "config")
        shutil.copytree(
            ROOT / "src/alice",
            output / "code/alice",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        for name in ("uv.lock", "pyproject.toml"):
            shutil.copyfile(ROOT / name, output / name)
        shutil.copyfile(
            ROOT / "hardware/bringup/face-speech-trial-v1.md", output / "procedure.md"
        )
        report["code_revision"] = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        report["input_sha256"] = hashlib.sha256(raw_source).hexdigest()
        print(
            f"Selected face channels {list(FACE_CHANNELS.values())}; "
            f"mode={report['actuation_mode']}; evidence={output}",
            flush=True,
        )
        asyncio.run(
            _run(
                clauses,
                output,
                hardware=args.enable_hardware,
                play=args.play,
                report=report,
                sad_hold_s=args.sad_hold_s,
            )
        )
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        report.update(outcome="failed", error=f"{type(exc).__name__}: {exc}")
        print(f"alice-face-speech: {report['error']}", file=sys.stderr, flush=True)
        return 130 if isinstance(exc, KeyboardInterrupt) else 2
    finally:
        if output is not None:
            report["artifacts"] = {
                str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(output.rglob("*"))
                if p.is_file() and p != output / "manifest.json"
            }
            _write(output / "manifest.json", report)


if __name__ == "__main__":
    raise SystemExit(main())
