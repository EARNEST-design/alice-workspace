"""Launch the local attended conversation dashboard in its idle state."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from alice.conversation.asr import QwenAsrClient
from alice.conversation.audio import Speaker
from alice.conversation.events import EventStore
from alice.conversation.models import TextModels
from alice.conversation.overlap import OverlapDetector
from alice.conversation.runtime import BenchRuntime
from alice.conversation.vad import Silero
from alice.conversation.web import serve


async def run(options: argparse.Namespace) -> None:
    store = EventStore()
    output = Speaker(
        store.emit,
        enabled=options.enable_audio,
        cantonese_model=options.cantonese_model,
        cantonese_source=options.cantonese_source,
        cantonese_python=options.cantonese_python,
    )
    languages = frozenset(
        {"English", "Cantonese"} if options.cantonese_model else {"English"}
    )
    models = TextModels(
        local=options.decision_url,
        expected_decision_run=(
            options.decision_checkpoint if options.decision_backend == "kev" else None
        ),
        decision_backend=options.decision_backend,
    )
    overlap = (
        await asyncio.to_thread(OverlapDetector, options.overlap_model)
        if options.overlap_model
        else None
    )
    runtime = BenchRuntime(
        store,
        QwenAsrClient(options.asr_url, languages=languages),
        models,
        output,
        vad_factory=lambda: Silero(options.vad_model),
        replay_path=options.replay_file,
        duration_s=options.duration,
        barge_in=options.experimental_barge_in,
        overlap_detector=overlap,
    )
    loop = asyncio.get_running_loop()
    done = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, done.set)
    last_bridge = 0.0

    async def dispatch(data: dict[str, Any]) -> dict[str, object]:
        nonlocal last_bridge
        action = data["action"]
        if action == "bridge":
            last_bridge = time.monotonic()
            store.emit("ros", "connected", message="ROS domain 74 observer heartbeat")
        elif action == "start":
            await runtime.start(data.get("mode", "listen"))
        elif action == "stop":
            await runtime.stop()
        elif action == "replay":
            await runtime.replay()
        else:
            raise ValueError("unknown action")
        return {"ok": True}

    def control(data: dict[str, Any]) -> dict[str, Any]:
        future = asyncio.run_coroutine_threadsafe(dispatch(data), loop)
        return future.result(timeout=4)

    server = serve(store, control, port=options.port)
    store.emit("ros", "disconnected", message="Observer not connected")
    store.emit(
        "output",
        "ready" if options.enable_audio else "dry",
        message="Live speaker enabled; replay remains dry"
        if options.enable_audio
        else "Dry speaker; relaunch with --enable-audio for output",
    )
    print(f"Conversation dashboard: http://127.0.0.1:{server.server_port}", flush=True)

    async def readiness() -> None:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            for stage, url, model in [
                ("asr", options.asr_url.rstrip("/") + "/models", "qwen3-asr"),
                (
                    "decision",
                    models.local + "/v1/models",
                    {
                        "kev": "kev-latest",
                        "minicpm": "minicpm5-2b",
                        "qwen": "qwen3.8-27b-mlx",
                    }[options.decision_backend],
                ),
                ("llm", "http://earnests-mac-studio:1234/v1/models", "qwen3.8-27b-mlx"),
            ]:
                try:
                    if stage == "decision" and options.decision_backend == "kev":
                        async with asyncio.timeout(3):
                            metadata = await models.check_decision_backend()
                        store.emit(
                            stage,
                            "ready",
                            message=metadata.get("run", model),
                            model=model,
                            backend="kev",
                        )
                        continue
                    response = await client.get(url)
                    response.raise_for_status()
                    key = "data"
                    if model not in [x["id"] for x in response.json()[key]]:
                        raise RuntimeError("configured model not advertised")
                    details: dict[str, Any] = {"message": model, "model": model}
                    if stage == "decision":
                        details["backend"] = options.decision_backend
                    if stage == "asr":
                        details["languages"] = sorted(languages)
                    store.emit(stage, "ready", **details)
                except Exception as error:
                    store.emit(stage, "unavailable", error=str(error)[:200])

    task = asyncio.create_task(readiness())
    try:
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), 1)
            except TimeoutError:
                pass
            if last_bridge and time.monotonic() - last_bridge > 5:
                store.emit("ros", "disconnected", message="Observer heartbeat expired")
                last_bridge = 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vad-model",
        type=Path,
        required=True,
        help="Qualified Silero 6.2.2 silero_vad.onnx (SHA256 checked)",
    )
    parser.add_argument(
        "--asr-url", default="https://work.manakin-gecko.ts.net:10000/v1"
    )
    parser.add_argument(
        "--replay-file",
        type=Path,
        help="Explicit synthetic 16k PCM16 WAV; replay never plays sound",
    )
    parser.add_argument(
        "--decision-backend",
        choices=("qwen", "minicpm", "kev"),
        default="qwen",
        help="Qwen is the tested default; local backends remain experimental",
    )
    parser.add_argument(
        "--decision-url", help="Override the selected local backend URL"
    )
    parser.add_argument(
        "--decision-checkpoint",
        default="jaredpalmer/kev-4b@c4bfa11b0dc07691884f2d97f1c4c4c05c92e416",
        help="Expected pinned Kev run from its /v1/models advertisement",
    )
    parser.add_argument(
        "--overlap-model", type=Path, help="Pinned pyannote segmentation ONNX export"
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--duration",
        type=float,
        default=300,
        help="Maximum microphone session seconds (1–300)",
    )
    parser.add_argument("--enable-audio", action="store_true")
    parser.add_argument(
        "--cantonese-model",
        type=Path,
        help="Local CosyVoice-300M-SFT snapshot; enables English/Cantonese replies",
    )
    parser.add_argument("--cantonese-source", type=Path)
    parser.add_argument("--cantonese-python", type=Path)
    parser.add_argument("--experimental-barge-in", action="store_true")
    options = parser.parse_args()
    if options.cantonese_model is not None:
        from alice.conversation.cantonese import MODEL_REVISION, SOURCE_REVISION

        options.cantonese_model = options.cantonese_model.expanduser().resolve()
        if (
            options.cantonese_model.name != MODEL_REVISION
            or not (options.cantonese_model / "cosyvoice.yaml").is_file()
        ):
            parser.error("--cantonese-model must name the qualified pinned snapshot")
        if options.cantonese_source is None or options.cantonese_python is None:
            parser.error("Cantonese requires --cantonese-source and --cantonese-python")
        options.cantonese_source = options.cantonese_source.expanduser().resolve()
        options.cantonese_python = options.cantonese_python.expanduser().absolute()
        if not os.access(options.cantonese_python, os.X_OK):
            parser.error("Cantonese Python executable is unavailable")
        result = subprocess.run(
            ["git", "-C", str(options.cantonese_source), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode or result.stdout.strip() != SOURCE_REVISION:
            parser.error("Cantonese source must be the qualified pinned checkout")
    elif options.cantonese_source is not None or options.cantonese_python is not None:
        parser.error("Cantonese source and Python require --cantonese-model")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    asyncio.run(run(options))


if __name__ == "__main__":
    main()
