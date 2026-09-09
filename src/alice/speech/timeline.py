"""Resolve speech and affect on the generated PCM sample clock."""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

from alice.contracts.affect import AffectIntent, UnitIntervalFloat
from alice.contracts.speech import SpeechPlan, SpeechSegment, SpeechSyncConfig, Vector


@dataclass(frozen=True)
class AudioClip:
    pcm: NDArray[np.float32]
    sample_rate: int

    def __post_init__(self) -> None:
        pcm = np.array(self.pcm, dtype=np.float32, copy=True)
        if (
            not isinstance(self.sample_rate, int)
            or self.sample_rate <= 0
            or pcm.ndim != 1
            or not len(pcm)
            or not np.isfinite(pcm).all()
            or np.max(np.abs(pcm)) > 1
        ):
            raise ValueError(
                "audio must be finite mono PCM in [-1, 1] at a positive rate"
            )
        pcm.setflags(write=False)
        object.__setattr__(self, "pcm", pcm)


class Synthesizer(Protocol):
    @property
    def identity(self) -> dict[str, str]: ...

    def synthesize(self, text: str, *, voice: str, seed: int) -> AudioClip: ...


class SegmentSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    segment_index: int
    start_sample: int
    end_sample: int


class SpeechFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sample_index: int = Field(ge=0)
    mouth_aperture: UnitIntervalFloat
    speech_weight: UnitIntervalFloat
    vector: Vector
    intensity: UnitIntervalFloat


@dataclass(frozen=True)
class PreparedSpeech:
    plan: SpeechPlan
    config: SpeechSyncConfig
    audio: AudioClip
    spans: tuple[SegmentSpan, ...]
    frames: tuple[SpeechFrame, ...]
    frame_samples: tuple[int, ...]
    model_identity: dict[str, str]

    def at_sample(self, sample_index: int) -> SpeechFrame:
        """Hold the most recent frame; never advance on synthesis wall time."""
        if not isinstance(sample_index, int) or sample_index < 0:
            raise ValueError("sample position must be a nonnegative integer")
        return self.frames[bisect_right(self.frame_samples, sample_index) - 1]

    def affect_intent(
        self, sample_index: int, *, now_ns: int, captured_at: datetime
    ) -> AffectIntent | None:
        frame = self.at_sample(sample_index)
        if sample_index >= len(self.audio.pcm):
            return None
        return AffectIntent(
            schema_version="affect-intent/v1",
            affect_schema_id=self.plan.affect_schema_id,
            vector=frame.vector,
            intensity=frame.intensity,
            source_id=self.plan.source_id,
            captured_at=captured_at,
            received_monotonic_ns=now_ns,
            expires_monotonic_ns=now_ns + 100_000_000,
        )


def _cue_at(segment: SpeechSegment, progress: float) -> tuple[Vector, float]:
    cues = segment.cues
    for left, right in zip(cues, cues[1:]):
        if progress < right.progress:
            mix = (progress - left.progress) / (right.progress - left.progress)
            values = tuple(
                a + mix * (b - a)
                for a, b in zip(left.vector, right.vector, strict=True)
            )
            return (values[0], values[1], values[2]), (
                left.intensity + mix * (right.intensity - left.intensity)
            )
    return cues[-1].vector, cues[-1].intensity


def prepare_speech(
    plan: SpeechPlan,
    synthesizer: Synthesizer,
    config: SpeechSyncConfig | None = None,
) -> PreparedSpeech:
    """Buffer a bounded plan and compute sample-aligned aperture and affect."""
    config = config or SpeechSyncConfig()
    chunks: list[NDArray[np.float32]] = []
    spans: list[SegmentSpan] = []
    cursor = 0
    sample_rate = 0
    for index, segment in enumerate(plan.segments):
        clip = synthesizer.synthesize(
            segment.text, voice=plan.voice, seed=(plan.seed + index) % 2**32
        )
        if sample_rate and sample_rate != clip.sample_rate:
            raise ValueError("synthesizer changed sample rate within utterance")
        sample_rate = clip.sample_rate
        if sample_rate < config.cadence_hz:
            raise ValueError("sample rate must cover synchronization cadence")
        pause = round(segment.pause_after_s * sample_rate)
        if (cursor + len(clip.pcm) + pause) / sample_rate > config.max_duration_s:
            raise ValueError("synthesized speech exceeds configured duration limit")
        spans.append(
            SegmentSpan(
                segment_index=index,
                start_sample=cursor,
                end_sample=cursor + len(clip.pcm),
            )
        )
        chunks.extend((clip.pcm, np.zeros(pause, dtype=np.float32)))
        cursor += len(clip.pcm) + pause
    speech_end = cursor
    chunks.append(np.zeros(round(config.tail_s * sample_rate), dtype=np.float32))
    audio = AudioClip(np.concatenate(chunks), sample_rate)
    hop = max(1, round(sample_rate / config.cadence_hz))
    aperture = 0.0
    frames: list[SpeechFrame] = []
    span_index = 0
    for sample in range(0, len(audio.pcm), hop):
        while (
            span_index + 1 < len(spans) and sample >= spans[span_index + 1].start_sample
        ):
            span_index += 1
        span = spans[span_index]
        progress = min(
            1.0, (sample - span.start_sample) / (span.end_sample - span.start_sample)
        )
        vector, intensity = _cue_at(plan.segments[span_index], progress)
        window = audio.pcm[sample : sample + hop].astype(np.float64)
        rms = float(np.sqrt(np.mean(window * window)))
        desired = min(
            1.0,
            max(
                0.0,
                (rms - config.noise_gate_rms)
                / (config.full_open_rms - config.noise_gate_rms),
            ),
        )
        tau = config.attack_s if desired > aperture else config.release_s
        aperture += (desired - aperture) * -math.expm1(-hop / sample_rate / tau)
        if desired == 0 and aperture < 0.01:
            aperture = 0.0
        # Fade ownership during trailing silence, after the jaw has closed.
        tail_progress = max(0.0, (sample - speech_end) / sample_rate / config.tail_s)
        weight = 1.0 - max(0.0, min(1.0, (tail_progress - 0.5) * 2))
        frames.append(
            SpeechFrame(
                sample_index=sample,
                mouth_aperture=aperture,
                speech_weight=weight,
                vector=vector,
                intensity=intensity,
            )
        )
    frames.append(
        SpeechFrame(
            sample_index=len(audio.pcm),
            mouth_aperture=0,
            speech_weight=0,
            vector=(0, 0, 0),
            intensity=0,
        )
    )
    return PreparedSpeech(
        plan,
        config,
        audio,
        tuple(spans),
        tuple(frames),
        tuple(f.sample_index for f in frames),
        dict(synthesizer.identity),
    )
