"""Bounded PCM transport and incremental envelopes; no model or serial calls."""

from __future__ import annotations

import asyncio
import math
import threading
from bisect import bisect_right
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from alice.contracts.speech import SpeechSyncConfig
from alice.contracts.speech_stream import SpeechClause
from alice.speech.timeline import SpeechFrame
from alice.speech.tts_worker import PcmChunk


class PcmTimeline:
    """Absolute sample offsets, bounded envelope history and onset-only cues.

    No PCM history is retained beyond the incomplete analysis window. The
    envelope/cue history is bounded by the configured utterance duration.
    This object has one owner outside the audio callback.
    """

    def __init__(
        self, sample_rate: int, generation_id: str, config: SpeechSyncConfig
    ) -> None:
        if type(sample_rate) is not int or not 8000 <= sample_rate <= 192000:
            raise ValueError("invalid timeline sample rate")
        self.sample_rate, self.generation_id, self.config = (
            sample_rate,
            generation_id,
            config,
        )
        self.generated_samples = 0
        self.finished = False
        self._hop = round(sample_rate / config.cadence_hz)
        self._pending = np.empty(0, np.float32)
        self._envelope: list[float] = []
        self._aperture = 0.0
        self._cues: list[tuple[int, SpeechClause]] = []
        self._clause: SpeechClause | None = None
        self._sequence = 0
        self._clause_final = True
        self._speech_end: int | None = None
        self._silences: list[tuple[int, int]] = []

    @property
    def envelope_samples(self) -> int:
        return min(len(self._envelope) * self._hop, self.generated_samples)

    @property
    def cues(self) -> tuple[tuple[int, SpeechClause], ...]:
        return tuple(self._cues)

    def commit_clause(self, clause: SpeechClause) -> None:
        if (
            self.finished
            or not self._clause_final
            or clause.generation_id != self.generation_id
            or clause.sequence != len(self._cues)
            or len(self._cues) >= 32
        ):
            raise ValueError("cannot replace or reorder timeline clauses")
        self._clause, self._sequence, self._clause_final = clause, 0, False
        self._cues.append((self.generated_samples, clause))

    def append(self, chunk: PcmChunk) -> None:
        if (
            self.finished
            or self._clause_final
            or self._clause is None
            or chunk.generation_id != self.generation_id
            or chunk.clause_id != self._clause.clause_id
            or chunk.sequence != self._sequence
            or chunk.sample_rate != self.sample_rate
        ):
            raise ValueError("stale or out-of-order timeline PCM")
        self._append_pcm(chunk.pcm)
        self._sequence += 1
        self._clause_final = chunk.final

    def _append_pcm(self, pcm: NDArray[np.float32]) -> None:
        if self.generated_samples + len(pcm) > round(
            (self.config.max_duration_s + self.config.tail_s) * self.sample_rate
        ):
            raise ValueError("stream exceeds configured duration bound")
        pending = np.concatenate((self._pending, pcm))
        full = len(pending) // self._hop * self._hop
        for start in range(0, full, self._hop):
            self._window(pending[start : start + self._hop])
        self._pending = pending[full:].copy()
        self.generated_samples += len(pcm)

    def _window(self, pcm: NDArray[np.float32]) -> None:
        window = pcm.astype(np.float64)
        rms = float(np.sqrt(np.mean(window * window)))
        desired = min(
            1.0,
            max(
                0.0,
                (rms - self.config.noise_gate_rms)
                / (self.config.full_open_rms - self.config.noise_gate_rms),
            ),
        )
        tau = (
            self.config.attack_s if desired > self._aperture else self.config.release_s
        )
        self._aperture += (desired - self._aperture) * -math.expm1(
            -self._hop / self.sample_rate / tau
        )
        if desired == 0 and self._aperture < 0.01:
            self._aperture = 0.0
        self._envelope.append(self._aperture)

    def append_silence(self, samples: int) -> NDArray[np.float32]:
        if (
            self.finished
            or not self._clause_final
            or type(samples) is not int
            or not 0 < samples <= self.sample_rate * 5
            or len(self._silences) >= 32
        ):
            raise ValueError("invalid deliberate silence")
        self._silences.append(
            (self.generated_samples, self.generated_samples + samples)
        )
        pcm = np.zeros(samples, np.float32)
        self._append_pcm(pcm)
        return pcm

    def finish(self) -> NDArray[np.float32]:
        if self.finished or not self._clause_final or not self.generated_samples:
            raise ValueError("cannot finish incomplete or empty PCM stream")
        self._speech_end = self.generated_samples
        tail = np.zeros(round(self.config.tail_s * self.sample_rate), np.float32)
        self._append_pcm(tail)
        if len(self._pending):
            self._window(self._pending)
            self._pending = np.empty(0, np.float32)
        self.finished = True
        return tail

    def frame_at(self, sample_index: int) -> SpeechFrame:
        if type(sample_index) is not int or sample_index < 0:
            raise ValueError("invalid sample position")
        if self.finished and sample_index >= self.generated_samples:
            return SpeechFrame(
                sample_index=sample_index,
                mouth_aperture=0,
                speech_weight=0,
                vector=(0, 0, 0),
                intensity=0,
            )
        if sample_index >= self.envelope_samples or not self._cues:
            raise BufferError("mouth lookahead is not generated yet")
        index = bisect_right([start for start, _ in self._cues], sample_index) - 1
        clause = self._cues[index][1]
        aperture = self._envelope[sample_index // self._hop]
        if any(start <= sample_index < end for start, end in self._silences):
            aperture = 0.0
        tail_progress = (
            0.0
            if self._speech_end is None
            else max(0, sample_index - self._speech_end)
            / self.sample_rate
            / self.config.tail_s
        )
        weight = 1 - min(1, max(0, (tail_progress - 0.5) * 2))
        return SpeechFrame(
            sample_index=sample_index,
            mouth_aperture=aperture,
            speech_weight=weight,
            vector=clause.vector,
            intensity=clause.intensity,
        )

    def led_frame(self, played_sample: int, lead_s: float = 0.1) -> SpeechFrame:
        if not math.isfinite(lead_s) or not 0 <= lead_s <= 0.5:
            raise ValueError("invalid mouth lead")
        audible = self.frame_at(played_sample)
        mouth = self.frame_at(played_sample + round(lead_s * self.sample_rate))
        return audible.model_copy(update={"mouth_aperture": mouth.mouth_aperture})


class PcmRingBuffer:
    """Fixed allocation; producer waits, callback never waits for input data."""

    def __init__(self, capacity: int) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 384000:
            raise ValueError("invalid PCM capacity")
        self.capacity = capacity
        self._pcm = np.empty(capacity, np.float32)
        self._read = self._write = self._depth = 0
        self._lock = threading.Lock()
        self.finished = False
        self.aborted = False
        self.max_depth = 0

    @property
    def depth(self) -> int:
        with self._lock:
            return self._depth

    async def put(self, pcm: NDArray[np.float32]) -> None:
        offset = 0
        while offset < len(pcm):
            with self._lock:
                if self.aborted or self.finished:
                    raise RuntimeError("PCM buffer closed")
                count = min(
                    len(pcm) - offset,
                    self.capacity - self._depth,
                    self.capacity - self._write,
                )
                self._pcm[self._write : self._write + count] = pcm[
                    offset : offset + count
                ]
                self._write = (self._write + count) % self.capacity
                self._depth += count
                self.max_depth = max(self.max_depth, self._depth)
            offset += count
            if not count:
                await asyncio.sleep(0.002)

    def read_into(self, out: NDArray[np.float32]) -> int:
        """Return -1 for accidental starvation; never replay previous data."""
        out.fill(0)
        with self._lock:
            if self.aborted or (self._depth < len(out) and not self.finished):
                return -1
            total = min(len(out), self._depth)
            first = min(total, self.capacity - self._read)
            out[:first, 0] = self._pcm[self._read : self._read + first]
            out[first:total, 0] = self._pcm[: total - first]
            self._read = (self._read + total) % self.capacity
            self._depth -= total
            return total

    def finish(self) -> None:
        with self._lock:
            if not self.aborted:
                self.finished = True

    def abort(self) -> None:
        with self._lock:
            self.aborted = True
            self._depth = 0


class StreamingAudioPlayback:
    """Callback copies PCM and records DAC origin; the caller handles stop codes."""

    def __init__(self, ring: PcmRingBuffer, sample_rate: int) -> None:
        self.ring, self.sample_rate = ring, sample_rate
        self.submitted_samples = 0
        self.error: str | None = None
        self.underflows = 0
        self._dac_start: float | None = None

    def callback(
        self, outdata: NDArray[np.float32], frames: int, timing: Any, status: Any
    ) -> Literal["continue", "stop", "abort"]:
        outdata.fill(0)
        if status or self.ring.aborted:
            self.error = f"audio output lost synchronization: {status or 'aborted'}"
            self.underflows += bool(status)
            return "abort"
        count = self.ring.read_into(outdata[:frames])
        if count < 0:
            self.error = "audio PCM underflow"
            self.underflows += 1
            return "abort"
        if self._dac_start is None:
            self._dac_start = float(timing.outputBufferDacTime)
        self.submitted_samples += count
        return "stop" if self.ring.finished and not self.ring.depth else "continue"

    def sample_position(self, device_time: float) -> int | None:
        if self._dac_start is None or device_time < self._dac_start:
            return None
        return min(
            self.submitted_samples,
            max(
                0, math.floor((device_time - self._dac_start) * self.sample_rate + 1e-6)
            ),
        )
