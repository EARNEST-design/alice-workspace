"""Local, explicitly identified VAD and bounded audio endpointing."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

SILERO_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
RATE = 16000
FRAME = 512
_MAX_UTTERANCE_SAMPLES = RATE * 15
_ENDPOINT_SILENCE_SAMPLES = FRAME * 10


@dataclass(frozen=True)
class VadUpdate:
    speaking: bool
    started: bool
    silence_ms: int
    audio: NDArray[np.float32] | None = None
    speech_probability_mean: float | None = None
    voiced_fraction: float | None = None
    voiced_seconds: float | None = None
    discard_reason: str | None = None


class Endpoint:
    """Endpoint speech with bounded audio and post-trigger VAD evidence.

    Probability statistics cover every frame from the triggering speech frame
    through the endpoint silence tail. Preroll audio is retained in the emitted
    waveform but excluded from the probability denominator because it predates
    the trigger.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.preroll = np.empty(0, np.float32)
        self.chunks: list[NDArray[np.float32]] = []
        self.samples = self.voiced = self.silence = 0
        self.probability_sum = 0.0
        self.probability_frames = 0
        self.speaking = False
        self.discarding = False
        self.recovery_silence = 0

    def _discard_overlong(self) -> VadUpdate:
        self.preroll = np.empty(0, np.float32)
        self.chunks = []
        self.samples = self.voiced = self.silence = 0
        self.probability_sum = 0.0
        self.probability_frames = 0
        self.speaking = False
        self.discarding = True
        self.recovery_silence = 0
        return VadUpdate(
            False,
            False,
            0,
            discard_reason="overlong_utterance",
        )

    def push(self, frame: NDArray[np.float32], probability: float) -> VadUpdate:
        if (
            frame.shape != (FRAME,)
            or not np.isfinite(frame).all()
            or np.any(np.abs(frame) > 1)
            or not math.isfinite(probability)
            or not 0 <= probability <= 1
        ):
            raise ValueError("invalid VAD frame/probability")
        if self.discarding:
            if probability < 0.35:
                self.recovery_silence += FRAME
            else:
                self.recovery_silence = 0
            silence_ms = self.recovery_silence * 1000 // RATE
            if self.recovery_silence >= _ENDPOINT_SILENCE_SAMPLES:
                self.discarding = False
                self.recovery_silence = 0
            return VadUpdate(False, False, silence_ms)

        started = False
        if probability >= 0.5 and not self.speaking:
            self.speaking = started = True
            self.chunks = [self.preroll.copy()]
            self.samples = len(self.preroll)
        if self.speaking:
            if self.samples + FRAME > _MAX_UTTERANCE_SAMPLES:
                return self._discard_overlong()
            self.chunks.append(frame.copy())
            self.samples += FRAME
            self.probability_sum += probability
            self.probability_frames += 1
            if probability >= 0.5:
                self.voiced += FRAME
            self.silence = self.silence + FRAME if probability < 0.35 else 0
            if self.silence >= _ENDPOINT_SILENCE_SAMPLES:
                silence_ms = self.silence * 1000 // RATE
                audio = np.concatenate(self.chunks) if self.voiced >= 1536 else None
                probability_mean = (
                    self.probability_sum / self.probability_frames
                    if audio is not None
                    else None
                )
                voiced_fraction = (
                    self.voiced / (self.probability_frames * FRAME)
                    if audio is not None
                    else None
                )
                voiced_seconds = self.voiced / RATE if audio is not None else None
                self.reset()
                return VadUpdate(
                    False,
                    started,
                    silence_ms,
                    audio,
                    probability_mean,
                    voiced_fraction,
                    voiced_seconds,
                )
        else:
            self.preroll = np.concatenate((self.preroll, frame))[-3200:]
        return VadUpdate(self.speaking, started, self.silence * 1000 // RATE)


class Silero:
    def __init__(self, model_path: Path) -> None:
        if hashlib.sha256(model_path.read_bytes()).hexdigest() != SILERO_SHA256:
            raise ValueError("Silero model hash differs from qualified 6.2.2 ONNX")
        import onnxruntime as ort  # type: ignore[import-untyped]

        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        self.session: Any = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"], sess_options=options
        )
        self.reset()
        self.probability(np.zeros(FRAME, np.float32))
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), np.float32)
        self.context = np.zeros((1, 64), np.float32)

    def probability(self, frame: NDArray[np.float32]) -> float:
        if frame.shape != (FRAME,) or not np.isfinite(frame).all():
            raise ValueError("invalid Silero input")
        batch = frame.reshape(1, FRAME)
        output, self.state = self.session.run(
            None,
            {
                "input": np.concatenate((self.context, batch), axis=1),
                "state": self.state,
                "sr": np.array(RATE, dtype=np.int64),
            },
        )
        self.context = batch[:, -64:].copy()
        return float(output[0, 0])
