"""Bounded overlap-evidence tests with synthetic model posteriors."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

MODEL_SHA256 = "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079"
FRAME_SECONDS = 270 / 16_000


def log_posteriors(labels: np.ndarray) -> np.ndarray:
    probabilities = np.full((*labels.shape, 7), 0.01, dtype=np.float32)
    np.put_along_axis(probabilities, labels[..., None], 0.94, axis=-1)
    return np.log(probabilities)


class FakeSession:
    def __init__(self, output: np.ndarray, *, metadata: dict[str, str] | None = None):
        self.output = output
        self.inputs: list[np.ndarray] = []
        self.metadata = metadata or {
            "model_type": "pyannote-segmentation-3.0",
            "sample_rate": "16000",
            "window_size": "160000",
            "receptive_field_size": "991",
            "receptive_field_shift": "270",
            "num_speakers": "3",
            "powerset_max_classes": "2",
            "num_classes": "7",
        }

    def get_inputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="x", shape=["N", 1, "T"], type="tensor(float)")]

    def get_outputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="y", shape=["N", "T", 7], type="tensor(float)")]

    def get_modelmeta(self) -> SimpleNamespace:
        return SimpleNamespace(custom_metadata_map=self.metadata)

    def run(
        self, output_names: list[str], feeds: dict[str, np.ndarray]
    ) -> list[np.ndarray]:
        assert output_names == ["y"]
        model_input = feeds["x"]
        self.inputs.append(model_input.copy())
        return [self.output[: model_input.shape[0]].copy()]


def make_detector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: np.ndarray,
    *,
    metadata: dict[str, str] | None = None,
) -> tuple[Any, FakeSession, dict[str, Any]]:
    import alice.conversation.overlap as overlap

    model = tmp_path / "model.onnx"
    model.write_bytes(b"test model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    monkeypatch.setattr(overlap, "EXPECTED_MODEL_SHA256", digest)
    session = FakeSession(output, metadata=metadata)
    observed: dict[str, Any] = {}

    def create_session(
        path: str, *, sess_options: Any, providers: list[str]
    ) -> FakeSession:
        observed.update(
            path=path,
            intra=sess_options.intra_op_num_threads,
            inter=sess_options.inter_op_num_threads,
            providers=providers,
        )
        return session

    monkeypatch.setattr(overlap, "_create_session", create_session)
    return overlap.OverlapDetector(model), session, observed


def test_model_pin_and_evidence_json_contract() -> None:
    from alice.conversation.overlap import EXPECTED_MODEL_SHA256, OverlapEvidence

    assert EXPECTED_MODEL_SHA256 == MODEL_SHA256
    evidence = OverlapEvidence(
        overlap_probability_mean=0.25,
        overlap_fraction=0.2,
        overlap_seconds=0.4,
        speech_fraction=0.75,
        speech_seconds=1.5,
        max_simultaneous_speakers=2,
        max_window_speakers=3,
        model="pinned-model",
    )
    assert evidence.as_dict() == {
        "overlap_probability_mean": 0.25,
        "overlap_fraction": 0.2,
        "overlap_seconds": 0.4,
        "speech_fraction": 0.75,
        "speech_seconds": 1.5,
        "max_simultaneous_speakers": 2,
        "max_window_speakers": 3,
        "model": "pinned-model",
    }


def test_analyze_uses_valid_unpadded_frames_and_powerset_probabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = np.zeros((1, 589), dtype=np.int64)
    labels[0, 10:30] = 1
    labels[0, 30:42] = 4
    labels[0, 42:56] = 2
    labels[0, 56:] = 6  # padded audio must not count as overlap
    detector, session, observed = make_detector(
        tmp_path, monkeypatch, log_posteriors(labels)
    )

    evidence = detector.analyze(np.zeros(16_000, dtype=np.float32))

    assert evidence.overlap_probability_mean == pytest.approx(
        (12 * 0.96 + 44 * 0.03) / 56
    )
    assert evidence.overlap_fraction == pytest.approx(12 / 56)
    assert evidence.overlap_seconds == pytest.approx(12 * FRAME_SECONDS)
    assert evidence.speech_fraction == pytest.approx(46 / 56)
    assert evidence.speech_seconds == pytest.approx(46 * FRAME_SECONDS)
    assert evidence.max_simultaneous_speakers == 2
    assert evidence.max_window_speakers == 2
    assert evidence.model.endswith(hashlib.sha256(b"test model").hexdigest())
    assert session.inputs[0].shape == (1, 1, 160_000)
    assert session.inputs[0].dtype == np.float32
    assert observed == {
        "path": str(tmp_path / "model.onnx"),
        "intra": 1,
        "inter": 1,
        "providers": ["CPUExecutionProvider"],
    }


def test_fifteen_seconds_uses_two_windows_without_cross_window_speaker_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = np.zeros((2, 589), dtype=np.int64)
    labels[0, :12] = 1
    labels[1, :12] = 2
    labels[1, 293:] = 4  # padding in the five-second window is excluded
    detector, session, _ = make_detector(tmp_path, monkeypatch, log_posteriors(labels))

    evidence = detector.analyze(np.zeros(15 * 16_000, dtype=np.float32))

    assert session.inputs[0].shape == (2, 1, 160_000)
    assert evidence.overlap_seconds == 0
    assert evidence.max_simultaneous_speakers == 1
    assert evidence.max_window_speakers == 1
    assert evidence.speech_seconds == pytest.approx(24 * FRAME_SECONDS)


def test_three_window_local_channels_require_minimum_active_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = np.zeros((1, 589), dtype=np.int64)
    labels[0, 20:32] = 1
    labels[0, 100:112] = 2
    labels[0, 200:212] = 3
    detector, _, _ = make_detector(tmp_path, monkeypatch, log_posteriors(labels))

    evidence = detector.analyze(np.zeros(10 * 16_000, dtype=np.float32))

    assert evidence.max_window_speakers == 3
    assert evidence.max_simultaneous_speakers == 1


def test_minimum_receptive_field_is_a_valid_short_utterance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = np.full((1, 589), 6, dtype=np.int64)
    labels[0, 0] = 1
    detector, session, _ = make_detector(tmp_path, monkeypatch, log_posteriors(labels))

    evidence = detector.analyze(np.zeros(991, dtype=np.float32))

    assert session.inputs[0].shape == (1, 1, 160_000)
    assert evidence.speech_seconds == pytest.approx(FRAME_SECONDS)
    assert evidence.overlap_seconds == 0


@pytest.mark.parametrize("tail_samples", [1, 990])
def test_sub_receptive_tail_after_ten_seconds_is_not_inferred_or_counted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tail_samples: int,
) -> None:
    labels = np.zeros((2, 589), dtype=np.int64)
    labels[1] = 4
    detector, session, _ = make_detector(tmp_path, monkeypatch, log_posteriors(labels))

    evidence = detector.analyze(np.zeros(160_000 + tail_samples, dtype=np.float32))

    assert session.inputs[0].shape == (1, 1, 160_000)
    assert evidence.overlap_seconds == 0
    assert evidence.max_simultaneous_speakers == 0


@pytest.mark.parametrize(
    "pcm",
    [
        np.zeros(990, dtype=np.float32),
        np.zeros(240_001, dtype=np.float32),
        np.zeros((16_000, 1), dtype=np.float32),
        np.zeros(16_000, dtype=np.float64),
        np.full(16_000, np.nan, dtype=np.float32),
        np.full(16_000, 1.01, dtype=np.float32),
    ],
)
def test_analyze_rejects_malformed_or_unbounded_pcm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pcm: np.ndarray
) -> None:
    detector, _, _ = make_detector(
        tmp_path,
        monkeypatch,
        log_posteriors(np.zeros((2, 589), dtype=np.int64)),
    )

    with pytest.raises(ValueError, match="PCM"):
        detector.analyze(pcm)


@pytest.mark.parametrize(
    "output",
    [
        np.zeros((1, 589, 6), dtype=np.float32),
        np.full((1, 589, 7), np.nan, dtype=np.float32),
        np.zeros((1, 589, 7), dtype=np.float32),
    ],
)
def test_analyze_rejects_malformed_model_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: np.ndarray
) -> None:
    detector, _, _ = make_detector(tmp_path, monkeypatch, output)

    with pytest.raises(RuntimeError, match="model output"):
        detector.analyze(np.zeros(16_000, dtype=np.float32))


def test_constructor_rejects_unpinned_or_incompatible_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alice.conversation.overlap as overlap

    model = tmp_path / "model.onnx"
    model.write_bytes(b"not the pinned model")
    with pytest.raises(ValueError, match="SHA-256"):
        overlap.OverlapDetector(model)

    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    monkeypatch.setattr(overlap, "EXPECTED_MODEL_SHA256", digest)
    bad_metadata = FakeSession(
        log_posteriors(np.zeros((1, 589), dtype=np.int64)),
        metadata={"model_type": "different"},
    )
    monkeypatch.setattr(
        overlap,
        "_create_session",
        lambda *_args, **_kwargs: bad_metadata,
    )
    with pytest.raises(ValueError, match="metadata"):
        overlap.OverlapDetector(model)
