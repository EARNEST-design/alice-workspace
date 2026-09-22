"""Cantonese voice identity and shared speech-level adjustment."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

MODEL_REPOSITORY = "FunAudioLLM/CosyVoice-300M-SFT"
MODEL_REVISION = "fbb71de2afe387ed854eebd80b9f3d078c6b9869"
SOURCE_REVISION = "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
VOICE = "粤语女"


def normalize_speech(pcm: NDArray[np.float32]) -> NDArray[np.float32]:
    """Match short-clause speech levels before the unchanged speaker peak cap."""
    peak = float(np.max(np.abs(pcm)))
    rms = float(np.sqrt(np.mean(pcm**2)))
    if rms < 1e-5:
        return pcm
    gain = min(8.0, 0.12 / rms, 0.95 / peak)
    return np.asarray(pcm * gain, dtype=np.float32)
