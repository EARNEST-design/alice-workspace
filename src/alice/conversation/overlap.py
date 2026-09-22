"""Pinned local powerset segmentation for bounded overlap evidence."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
from numpy.typing import NDArray

EXPECTED_MODEL_SHA256 = (
    "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079"
)
_MODEL_NAME = "sherpa-onnx-pyannote-segmentation-3.0"
_SAMPLE_RATE = 16_000
_MIN_SAMPLES = 991
_MAX_SAMPLES = 15 * _SAMPLE_RATE
_OVERLAP_THRESHOLD = 0.5
_SPEECH_THRESHOLD = 0.5
_WINDOW_SPEAKER_THRESHOLD = 0.5
_MIN_WINDOW_SPEAKER_SECONDS = 0.2


@dataclass(frozen=True, slots=True)
class OverlapEvidence:
    """Aggregate frame evidence; window-local channels are never identities."""

    overlap_probability_mean: float
    overlap_fraction: float
    overlap_seconds: float
    speech_fraction: float
    speech_seconds: float
    max_simultaneous_speakers: int
    max_window_speakers: int
    model: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _create_session(path: str, *, sess_options: Any, providers: list[str]) -> Any:
    return ort.InferenceSession(
        path,
        sess_options=sess_options,
        providers=providers,
    )


class OverlapDetector:
    """Analyze one 1–15 second, 16 kHz mono utterance on one CPU thread."""

    def __init__(self, model_path: Path) -> None:
        if not model_path.is_file():
            raise ValueError("overlap model path is not a file")
        digest = _sha256(model_path)
        if digest != EXPECTED_MODEL_SHA256:
            raise ValueError("overlap model SHA-256 does not match the pinned export")

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self._session = _create_session(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._validate_contract()
        metadata = self._session.get_modelmeta().custom_metadata_map
        self._window_size = int(metadata["window_size"])
        self._receptive_field_size = int(metadata["receptive_field_size"])
        self._frame_shift = int(metadata["receptive_field_shift"])
        self._frames_per_window = (
            (self._window_size - self._receptive_field_size) // self._frame_shift
        ) + 1
        self._frame_seconds = self._frame_shift / _SAMPLE_RATE
        self._input_name = self._session.get_inputs()[0].name
        self._output_name = self._session.get_outputs()[0].name
        self._lock = threading.Lock()
        self._model = f"{_MODEL_NAME}@sha256:{digest}"

    def _validate_contract(self) -> None:
        metadata = self._session.get_modelmeta().custom_metadata_map
        expected_metadata = {
            "model_type": "pyannote-segmentation-3.0",
            "sample_rate": "16000",
            "window_size": "160000",
            "receptive_field_size": "991",
            "receptive_field_shift": "270",
            "num_speakers": "3",
            "powerset_max_classes": "2",
            "num_classes": "7",
        }
        if any(metadata.get(key) != value for key, value in expected_metadata.items()):
            raise ValueError("overlap model metadata is incompatible")
        inputs = self._session.get_inputs()
        outputs = self._session.get_outputs()
        if (
            len(inputs) != 1
            or inputs[0].name != "x"
            or inputs[0].type != "tensor(float)"
            or len(inputs[0].shape) != 3
            or inputs[0].shape[1] != 1
            or len(outputs) != 1
            or outputs[0].name != "y"
            or outputs[0].type != "tensor(float)"
            or len(outputs[0].shape) != 3
            or outputs[0].shape[2] != 7
        ):
            raise ValueError("overlap model tensor contract is incompatible")

    def _windows(
        self, pcm: NDArray[np.float32]
    ) -> tuple[NDArray[np.float32], list[int]]:
        valid_samples = [
            min(self._window_size, len(pcm) - start)
            for start in range(0, len(pcm), self._window_size)
            if len(pcm) - start >= self._receptive_field_size
        ]
        windows = np.zeros(
            (len(valid_samples), 1, self._window_size),
            dtype=np.float32,
        )
        for index, count in enumerate(valid_samples):
            start = index * self._window_size
            windows[index, 0, :count] = pcm[start : start + count]
        return windows, valid_samples

    def _valid_frames(self, valid_samples: int) -> int:
        return max(
            0,
            ((valid_samples - self._receptive_field_size) // self._frame_shift) + 1,
        )

    def analyze(self, pcm: NDArray[np.float32]) -> OverlapEvidence:
        if (
            not isinstance(pcm, np.ndarray)
            or pcm.dtype != np.float32
            or pcm.ndim != 1
            or not _MIN_SAMPLES <= len(pcm) <= _MAX_SAMPLES
            or not np.isfinite(pcm).all()
            or np.any(np.abs(pcm) > 1)
        ):
            raise ValueError(
                "PCM must be finite bounded float32 mono at 16 kHz, "
                "at least 991 samples and at most 15 s"
            )
        windows, valid_samples = self._windows(np.ascontiguousarray(pcm))
        with self._lock:
            raw_outputs = self._session.run(
                [self._output_name], {self._input_name: windows}
            )
        if len(raw_outputs) != 1:
            raise RuntimeError("invalid overlap model output count")
        output = np.asarray(raw_outputs[0])
        if (
            output.dtype != np.float32
            or output.shape != (len(valid_samples), self._frames_per_window, 7)
            or not np.isfinite(output).all()
            or np.any(output > 1e-4)
        ):
            raise RuntimeError("invalid overlap model output tensor")
        probabilities = np.exp(output.astype(np.float64))
        if not np.allclose(probabilities.sum(axis=-1), 1.0, rtol=1e-3, atol=1e-4):
            raise RuntimeError("invalid overlap model output log probabilities")

        overlap_parts: list[NDArray[np.float64]] = []
        speech_parts: list[NDArray[np.float64]] = []
        max_window_speakers = 0
        for index, sample_count in enumerate(valid_samples):
            frame_count = self._valid_frames(sample_count)
            window = probabilities[index, :frame_count]
            overlap_parts.append(window[:, 4:7].sum(axis=-1))
            speech_parts.append(1.0 - window[:, 0])
            local_speaker_probabilities = np.stack(
                (
                    window[:, 1] + window[:, 4] + window[:, 5],
                    window[:, 2] + window[:, 4] + window[:, 6],
                    window[:, 3] + window[:, 5] + window[:, 6],
                ),
                axis=-1,
            )
            active_seconds = (
                local_speaker_probabilities > _WINDOW_SPEAKER_THRESHOLD
            ).sum(axis=0) * self._frame_seconds
            max_window_speakers = max(
                max_window_speakers,
                int(np.count_nonzero(active_seconds >= _MIN_WINDOW_SPEAKER_SECONDS)),
            )

        overlap_probability = np.concatenate(overlap_parts)
        speech_probability = np.concatenate(speech_parts)
        overlap_frames = overlap_probability > _OVERLAP_THRESHOLD
        speech_frames = speech_probability > _SPEECH_THRESHOLD
        if np.any(overlap_frames):
            max_simultaneous_speakers = 2
        elif np.any(speech_frames):
            max_simultaneous_speakers = 1
        else:
            max_simultaneous_speakers = 0
        return OverlapEvidence(
            overlap_probability_mean=float(overlap_probability.mean()),
            overlap_fraction=float(overlap_frames.mean()),
            overlap_seconds=float(overlap_frames.sum() * self._frame_seconds),
            speech_fraction=float(speech_frames.mean()),
            speech_seconds=float(speech_frames.sum() * self._frame_seconds),
            max_simultaneous_speakers=max_simultaneous_speakers,
            max_window_speakers=max_window_speakers,
            model=self._model,
        )
