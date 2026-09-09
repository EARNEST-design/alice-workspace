"""Portable speech preview and reproducible, hardware-free run artifacts."""

from __future__ import annotations

import base64
import hashlib
import json
import platform
import subprocess
import wave
from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from pathlib import Path
from typing import Any

import numpy as np

from alice.contracts.motion import TargetUpdateHorizon
from alice.contracts.speech import SpeechPlan
from alice.speech.composer import compose_frame, expression_at_sample
from alice.speech.timeline import PreparedSpeech


def write_artifacts(
    prepared: PreparedSpeech,
    output: Path,
    *,
    synthesis_seconds: float,
    expression: TargetUpdateHorizon | None = None,
) -> dict[str, Any]:
    """Create a new run directory; never overwrite an existing run."""
    output.mkdir(parents=True, exist_ok=False)
    audio = prepared.audio
    with wave.open(str(output / "speech.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(audio.sample_rate)
        wav.writeframes(np.rint(audio.pcm * 32767).astype("<i2").tobytes())
    updates = [
        compose_frame(
            expression_at_sample(
                expression, sample_index=f.sample_index, sample_rate=audio.sample_rate
            ),
            f,
            prepared.config,
        ).model_dump(mode="json")
        for f in prepared.frames
    ]
    timeline = {
        "schema_version": "speech-timeline/v1",
        "sample_rate": audio.sample_rate,
        "sample_count": len(audio.pcm),
        "spans": [span.model_dump() for span in prepared.spans],
        "frames": [frame.model_dump() for frame in prepared.frames],
        "motion": updates,
    }
    payloads = {
        "plan.json": prepared.plan.model_dump(mode="json"),
        "sync-config.json": prepared.config.model_dump(mode="json"),
        "speech-plan.schema.json": SpeechPlan.model_json_schema(),
        "timeline.json": timeline,
    }
    if expression is not None:
        payloads["expression.json"] = expression.model_dump(mode="json")
    for name, value in payloads.items():
        (output / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    template = files("alice.resources").joinpath("speech-preview.html").read_text()
    data = {"plan": payloads["plan.json"], "timeline": timeline}
    safe_data = json.dumps(data).replace("<", "\\u003c").replace("&", "\\u0026")
    preview = template.replace("__SPEECH_DATA__", safe_data).replace(
        "__AUDIO_BASE64__",
        base64.b64encode((output / "speech.wav").read_bytes()).decode(),
    )
    (output / "preview.html").write_text(preview, encoding="utf-8")
    packages: dict[str, str] = {}
    for package in ("alice", "numpy", "pydantic", "pocket-tts", "torch", "sounddevice"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = "not installed"
    root = Path(__file__).resolve().parents[3]
    revision_text = "unavailable"
    working_tree_dirty = None
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True
        )
        if revision.returncode == 0:
            revision_text = revision.stdout.strip()
        if dirty.returncode == 0:
            working_tree_dirty = bool(dirty.stdout)
    except OSError:
        pass  # Installed wheels and minimal containers need not include Git.
    duration = len(audio.pcm) / audio.sample_rate
    manifest = {
        "schema_version": "speech-run/v1",
        "actuation_mode": "none",
        "expression_mode": "supplied-proposal" if expression else "neutral-fallback",
        "model": prepared.model_identity,
        "voice": prepared.plan.voice,
        "seed": prepared.plan.seed,
        "packages": packages,
        "python": platform.python_version(),
        "code_revision": revision_text,
        "working_tree_dirty": working_tree_dirty,
        "synthesis_seconds": synthesis_seconds,
        "audio_duration_s": duration,
        "realtime_factor": synthesis_seconds / duration,
        "source": (
            "operator/LLM-authored text; generated audio; no participant recordings"
        ),
        "conclusion": (
            "Software sample synchronization only; servo response unmeasured."
        ),
        "artifacts": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(output.iterdir())
            if path.is_file()
        },
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
