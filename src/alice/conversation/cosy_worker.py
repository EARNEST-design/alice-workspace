"""Standalone CPU CosyVoice female preset worker; stdout is bounded PCM only."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import random
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
from numpy.typing import NDArray


@lru_cache(maxsize=1)
def _converter() -> Any:
    from opencc import OpenCC  # type: ignore[import-untyped]

    return OpenCC("t2s")


def voice_text(text: str) -> str:
    # This SFT preset reads simplified character forms more reliably. Keep the
    # Cantonese wording; displayed reply text remains Traditional Chinese.
    return str(_converter().convert(text))


def prepare_audio(pcm: NDArray[np.float32], rate: int) -> NDArray[np.float32]:
    from scipy.signal import resample_poly  # type: ignore[import-untyped]

    from alice.conversation.cantonese import normalize_speech

    if (
        rate != 22050
        or pcm.ndim != 1
        or not 0 < len(pcm) <= rate * 15
        or not np.isfinite(pcm).all()
        or np.any(np.abs(pcm) > 1)
    ):
        raise ValueError("invalid Cantonese female audio")
    return normalize_speech(resample_poly(pcm, 160, 147).astype(np.float32))


def synthesize_request(engine: Any, request: Any, output: BinaryIO) -> None:
    if not isinstance(request, dict):
        raise ValueError("invalid speech request")
    text, seed = request.get("text"), request.get("seed")
    if (
        not isinstance(text, str)
        or not text.strip()
        or len(text) > 200
        or type(seed) is not int
        or not 0 <= seed < 2**32
    ):
        raise ValueError("invalid speech text or seed")
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    pieces = []
    samples = 0
    with contextlib.redirect_stdout(sys.stderr):
        for item in engine.inference_sft(
            voice_text(text), "粤语女", stream=False, text_frontend=False, speed=0.85
        ):
            pcm = item["tts_speech"].detach().cpu().numpy()
            if pcm.ndim != 2 or pcm.shape[0] != 1:
                raise ValueError("invalid Cantonese female audio shape")
            samples += pcm.shape[1]
            if samples > 22050 * 15:
                raise ValueError("Cantonese female audio exceeds duration limit")
            pieces.append(pcm[0])
    if not pieces:
        raise ValueError("Cantonese female voice returned no audio")
    pcm = prepare_audio(np.concatenate(pieces), engine.sample_rate)
    for start in range(0, len(pcm), 24000):
        chunk = pcm[start : start + 24000]
        header = {"samples": len(chunk), "sample_rate": 24000}
        output.write(json.dumps(header).encode("ascii") + b"\n")
        output.write(chunk.astype("<f4").tobytes())
        output.flush()
    output.write(b'{"done":true}\n')
    output.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    # This file is launched by path under an isolated Python environment.
    sys.path[:0] = [
        str(Path(__file__).resolve().parents[2]),
        str(args.source),
        str(args.source / "third_party" / "Matcha-TTS"),
    ]
    logging.disable(logging.CRITICAL)
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from cosyvoice.cli.cosyvoice import CosyVoice  # type: ignore[import-not-found]
        from torch.ao.quantization import quantize_dynamic

        torch.set_num_threads(6)
        torch.set_num_interop_threads(1)
        engine = CosyVoice(str(args.model), load_jit=False, load_trt=False, fp16=False)
        if "粤语女" not in engine.list_available_spks():
            raise RuntimeError("Cantonese female preset is missing")
        engine.model.llm = quantize_dynamic(  # type: ignore[no-untyped-call]
            engine.model.llm, {torch.nn.Linear}, dtype=torch.qint8
        )
    while line := sys.stdin.buffer.readline(4097):
        if len(line) > 4096 or not line.endswith(b"\n"):
            raise ValueError("speech request exceeds limit")
        synthesize_request(engine, json.loads(line), sys.stdout.buffer)


if __name__ == "__main__":
    main()
