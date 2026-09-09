"""Optional CPU Pocket TTS adapter, intended for a dedicated speech worker."""

from __future__ import annotations

import hashlib
import os
import threading
from importlib.metadata import version
from typing import Any, get_args

import numpy as np

from alice.contracts.speech import Voice
from alice.speech.timeline import AudioClip

_MODEL_LOCK = threading.Lock()


class PocketSynthesizer:
    """Load one January model and cache preset states without hardware access.

    Pocket TTS changes Torch's global thread count on import and uses its RNG.
    We restore those settings, but concurrent ML should run in another process.
    """

    def __init__(self, *, offline: bool = False) -> None:
        self._offline = offline
        self._model: Any = None
        self._voices: dict[str, Any] = {}
        self._identity = {
            "backend": "pocket-tts",
            "model": "english_2026-01",
            "device": "cpu",
            "package_version": "3.1.0",
        }

    @property
    def identity(self) -> dict[str, str]:
        return dict(self._identity)

    def synthesize(self, text: str, *, voice: str, seed: int) -> AudioClip:
        if voice not in get_args(Voice):
            raise ValueError("only built-in preset voices are supported")
        if not text.strip() or len(text) > 1000 or not 0 <= seed < 2**32:
            raise ValueError("invalid text or seed")
        with _MODEL_LOCK:
            if self._offline:
                os.environ["HF_HUB_OFFLINE"] = "1"
                from huggingface_hub import constants

                if not constants.HF_HUB_OFFLINE:
                    raise RuntimeError(
                        "Offline mode needs HF_HUB_OFFLINE=1 before Python starts; "
                        "Hugging Face was already initialized online"
                    )
            try:
                import torch
            except ImportError as error:
                raise RuntimeError(
                    "Install speech dependencies: uv sync --extra speech"
                ) from error
            threads = torch.get_num_threads()
            try:
                with torch.random.fork_rng(devices=[]), torch.no_grad():
                    torch.manual_seed(seed)
                    from pocket_tts import TTSModel  # type: ignore[import-untyped]

                    torch.set_num_threads(2)
                    if self._model is None:
                        self._model = TTSModel.load_model(language="english_2026-01")
                        self._identity["package_version"] = version("pocket-tts")
                        config_json = self._model.config.model_dump_json()
                        self._identity["config_sha256"] = hashlib.sha256(
                            config_json.encode()
                        ).hexdigest()
                        self._identity["model_config"] = config_json
                        self._identity["voice_cloning_weights"] = str(
                            getattr(self._model, "has_voice_cloning", "unknown")
                        )
                    if voice not in self._voices:
                        self._voices[voice] = self._model.get_state_for_audio_prompt(
                            voice
                        )
                    # Loading consumes RNG on the first call. Reset so cold/warm
                    # requests with the same seed generate the same speech.
                    torch.manual_seed(seed)
                    tensor = self._model.generate_audio(
                        self._voices[voice], text, copy_state=True
                    )
                    pcm = tensor.detach().cpu().numpy()
                    if not np.isfinite(pcm).all():
                        raise ValueError("Pocket TTS returned nonfinite audio")
                    return AudioClip(
                        np.clip(pcm, -1, 1).astype(np.float32),
                        int(self._model.sample_rate),
                    )
            except ImportError as error:
                raise RuntimeError(
                    "Install speech dependencies: uv sync --extra speech"
                ) from error
            finally:
                torch.set_num_threads(threads)
