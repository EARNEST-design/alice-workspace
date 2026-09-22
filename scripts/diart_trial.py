"""CPU-only Diart qualification on declared synthetic WAVs, never microphone input.

Run with the separately frozen requirements/diart-cpu-py312.txt environment.
No model downloads, telemetry, playback, voice enrollment or service mutations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import time
from pathlib import Path

import numpy as np

SEGMENTATION_SHA = "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079"
EMBEDDING_SHA = "7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068"


def powerset_to_speakers(log_probabilities):
    """Match pyannote Powerset.to_multilabel's default hard conversion."""
    scores = np.asarray(log_probabilities)
    if (
        scores.ndim < 2
        or scores.shape[-1] != 7
        or not np.isfinite(scores).all()
        or not np.allclose(np.exp(scores).sum(axis=-1), 1, rtol=1e-3, atol=1e-4)
    ):
        raise ValueError("Invalid seven-class segmentation log probabilities")
    mapping = np.array(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 0], [1, 0, 1], [0, 1, 1]],
        dtype=np.float32,
    )
    return mapping[scores.argmax(axis=-1)]


def causal_windows(
    pcm,
    *,
    sample_rate=16000,
    window_seconds=5,
    step_seconds=0.5,
    latency_seconds=0.5,
):
    """Left-pad startup; flush only the requested aggregation delay at EOF."""
    window = round(window_seconds * sample_rate)
    step = round(step_seconds * sample_rate)
    latency = round(latency_seconds * sample_rate)
    if not 0 < step <= latency <= window:
        raise ValueError("Require 0 < step <= latency <= window")
    for end in range(
        step, math.ceil((len(pcm) + latency - step) / step) * step + 1, step
    ):
        start = end - window
        chunk = np.zeros(window, dtype=np.float32)
        left, right = max(0, start), min(len(pcm), end)
        if right > left:
            chunk[left - start : right - start] = pcm[left:right]
        yield start / sample_rate, chunk


def clip_segments(rows, duration):
    return [
        (max(0.0, start), min(duration, end), label)
        for start, end, label in rows
        if min(duration, end) > max(0.0, start)
    ]


def publication_segments(rows, *, audio_seconds, available_at, latency, step=0.5):
    """Ignore upstream's repeated startup prefix and keep only this emitted step."""
    start_at = max(0.0, available_at - latency)
    stop_at = min(audio_seconds, available_at - latency + step)
    return [
        (max(start, start_at), min(end, stop_at), label)
        for start, end, label in rows
        if min(end, stop_at) > max(start, start_at)
    ]


def check_model(path, expected):
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Unexpected model hash: {path.name}")


def make_pipeline(options):
    import onnxruntime as ort
    import torch
    from diart import SpeakerDiarization, SpeakerDiarizationConfig
    from diart.models import EmbeddingModel, SegmentationModel
    from pyannote.audio.pipelines.speaker_verification import (
        ONNXWeSpeakerPretrainedSpeakerEmbedding,
    )

    check_model(options.segmentation, SEGMENTATION_SHA)
    check_model(options.embedding, EMBEDDING_SHA)
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.manual_seed(7)
    np.random.seed(7)

    class LocalSegmentation:
        def __init__(self):
            settings = ort.SessionOptions()
            settings.intra_op_num_threads = 1
            settings.inter_op_num_threads = 1
            self.session = ort.InferenceSession(
                str(options.segmentation),
                sess_options=settings,
                providers=["CPUExecutionProvider"],
            )

        def to(self, device):
            if device.type != "cpu":
                raise ValueError("This qualification is CPU-only")
            return self

        def __call__(self, waveform):
            scores = self.session.run(["y"], {"x": waveform.cpu().numpy()})[0]
            return torch.from_numpy(powerset_to_speakers(scores))

    return SpeakerDiarization(
        SpeakerDiarizationConfig(
            segmentation=SegmentationModel(LocalSegmentation),
            embedding=EmbeddingModel(
                lambda: ONNXWeSpeakerPretrainedSpeakerEmbedding(
                    str(options.embedding),
                    device=torch.device("cpu"),
                )
            ),
            duration=options.window,
            step=0.5,
            latency=options.latency,
            tau_active=0.6,
            rho_update=options.rho_update,
            delta_new=options.delta_new,
            max_speakers=4,
            device=torch.device("cpu"),
            sample_rate=16000,
        )
    )


def run_file(pipeline, path, output_dir, options):
    import soundfile as sf
    from pyannote.core import Annotation, Segment, SlidingWindow, SlidingWindowFeature

    pcm, rate = sf.read(path, dtype="float32")
    if (
        rate != 16000
        or pcm.ndim != 1
        or not 0 < len(pcm) <= rate * 300
        or not np.isfinite(pcm).all()
        or np.max(np.abs(pcm)) > 1
    ):
        raise ValueError("Expected bounded mono 16kHz PCM, maximum 300 seconds")
    pipeline.reset()
    rows, emitted, timings = [], [], []
    began = time.monotonic()
    for start, chunk in causal_windows(
        pcm,
        window_seconds=options.window,
        latency_seconds=options.latency,
    ):
        waveform = SlidingWindowFeature(
            chunk[:, None],
            SlidingWindow(start=start, step=1 / rate, duration=1 / rate),
        )
        tick = time.monotonic()
        annotation, _ = pipeline([waveform])[0]
        milliseconds = (time.monotonic() - tick) * 1000
        timings.append(milliseconds)
        current = publication_segments(
            [
                (s.start, s.end, label)
                for s, _, label in annotation.itertracks(yield_label=True)
            ],
            audio_seconds=len(pcm) / rate,
            available_at=start + options.window,
            latency=options.latency,
        )
        rows.extend(current)
        emitted.append(
            {
                "available_at_audio_seconds": start + options.window,
                "compute_ms": milliseconds,
                "segments": current,
            }
        )
    annotation = Annotation(uri=path.stem)
    for index, (start, end, label) in enumerate(rows):
        annotation[Segment(start, end), index] = label
    annotation = annotation.support()
    with (output_dir / f"{path.stem}.rttm").open("w") as target:
        annotation.write_rttm(target)
    elapsed = time.monotonic() - began
    result = {
        "synthetic_input": str(path),
        "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "audio_seconds": len(pcm) / rate,
        "elapsed_seconds": elapsed,
        "real_time_factor": elapsed / (len(pcm) / rate),
        "step_ms": {
            "median": float(np.median(timings)),
            "p95": float(np.percentile(timings, 95)),
            "max": max(timings),
            "over_500ms": sum(t > 500 for t in timings),
        },
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "speaker_labels": annotation.labels(),
        "segments": [
            (s.start, s.end, label)
            for s, _, label in annotation.itertracks(yield_label=True)
        ],
        "stream_outputs": emitted,
    }
    (output_dir / f"{path.stem}.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"segments", "stream_outputs"}}
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segmentation", required=True, type=Path)
    parser.add_argument("--embedding", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--window", type=float, default=5)
    parser.add_argument("--latency", type=float, default=0.5)
    parser.add_argument("--rho-update", type=float, default=0.3)
    parser.add_argument("--delta-new", type=float, default=1.0)
    parser.add_argument("--synthetic-wavs", nargs="+", type=Path, required=True)
    options = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    options.output_dir.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    pipeline = make_pipeline(options)
    config = {
        k: str(v) if isinstance(v, Path) else v
        for k, v in vars(options).items()
        if k != "synthetic_wavs"
    }
    config.update(
        seed=7,
        sample_rate=16000,
        step_seconds=0.5,
        torch_threads=1,
        onnx_threads=1,
        pipeline_load_seconds=time.monotonic() - began,
        segmentation_sha256=SEGMENTATION_SHA,
        embedding_sha256=EMBEDDING_SHA,
    )
    (options.output_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    for path in options.synthetic_wavs:
        run_file(pipeline, path, options.output_dir, options)


if __name__ == "__main__":
    main()
