"""Load retained local speech without synthesis or device access."""

from __future__ import annotations

import hashlib
import io
import json
import wave
from pathlib import Path

import numpy as np
from pydantic import TypeAdapter

from alice.contracts.speech import SpeechPlan, SpeechSyncConfig
from alice.speech.timeline import AudioClip, PreparedSpeech, SegmentSpan, SpeechFrame


def load_recording(directory: Path) -> PreparedSpeech:
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("schema_version") != "speech-run/v1":
        raise ValueError("unsupported speech recording manifest")
    retained: dict[str, bytes] = {}
    for name in ("plan.json", "sync-config.json", "timeline.json", "speech.wav"):
        path = directory / name
        if path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("speech recording file is too large")
        contents = path.read_bytes()
        if hashlib.sha256(contents).hexdigest() != manifest.get("artifacts", {}).get(
            name
        ):
            raise ValueError(f"speech recording checksum mismatch: {name}")
        retained[name] = contents
    plan = SpeechPlan.model_validate_json(retained["plan.json"])
    config = SpeechSyncConfig.model_validate_json(retained["sync-config.json"])
    timeline = json.loads(retained["timeline.json"])
    with wave.open(io.BytesIO(retained["speech.wav"])) as wav:
        if (
            wav.getnchannels() != 1
            or wav.getsampwidth() != 2
            or wav.getcomptype() != "NONE"
        ):
            raise ValueError("speech recording must be mono PCM16")
        rate, count = wav.getframerate(), wav.getnframes()
        if not 0 < rate <= 192000 or not 0 < count <= rate * 610:
            raise ValueError("speech recording exceeds duration/rate limits")
        raw = wav.readframes(count)
    if len(raw) != count * 2:
        raise ValueError("truncated speech recording")
    audio = AudioClip(
        np.frombuffer(raw, dtype="<i2").astype(np.float32) / np.float32(32768), rate
    )
    if (
        timeline.get("schema_version") != "speech-timeline/v1"
        or timeline.get("sample_count") != count
        or timeline.get("sample_rate") != rate
    ):
        raise ValueError("speech timeline does not match PCM clock")
    frames = tuple(SpeechFrame.model_validate(f) for f in timeline["frames"])
    samples = tuple(frame.sample_index for frame in frames)
    hop = max(1, round(rate / config.cadence_hz))
    if samples != (*range(0, count, hop), count):
        raise ValueError("speech timeline samples are not ordered on the PCM clock")
    spans = tuple(SegmentSpan.model_validate(s) for s in timeline["spans"])
    cursor = 0
    if len(spans) != len(plan.segments):
        raise ValueError("speech segment count mismatch")
    for index, (span, segment) in enumerate(zip(spans, plan.segments, strict=True)):
        if (
            span.segment_index != index
            or span.start_sample != cursor
            or not cursor < span.end_sample <= count
        ):
            raise ValueError("invalid speech segment span")
        cursor = span.end_sample + round(segment.pause_after_s * rate)
    if cursor + round(config.tail_s * rate) != count:
        raise ValueError("speech tail does not match the PCM clock")
    if frames[-1].speech_weight != 0 or frames[-1].mouth_aperture != 0:
        raise ValueError("speech recording has no terminal release")
    identity = TypeAdapter(dict[str, str]).validate_python(manifest["model"])
    return PreparedSpeech(plan, config, audio, spans, frames, samples, identity)
