"""Contracts that protect the optional local diarization experiment."""

import runpy
from pathlib import Path

import numpy as np
import pytest


def trial():
    path = Path(__file__).parents[2] / "scripts" / "diart_trial.py"
    assert path.exists(), "local Diart trial adapter is not implemented"
    return runpy.run_path(str(path))


def test_all_powerset_classes_preserve_simultaneous_speakers():
    probabilities = np.full((7, 7), 0.001, dtype=np.float32)
    np.fill_diagonal(probabilities, 0.994)
    actual = trial()["powerset_to_speakers"](np.log(probabilities))
    np.testing.assert_array_equal(
        actual,
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 0], [1, 0, 1], [0, 1, 1]],
    )


def test_invalid_segmentation_does_not_invent_a_speaker():
    convert = trial()["powerset_to_speakers"]
    for bad in [np.zeros((2, 7)), np.full((2, 7), np.nan), np.zeros((2, 3))]:
        with pytest.raises(ValueError):
            convert(bad)


def test_rolling_windows_never_read_future_audio_and_flush_delay():
    windows = list(
        trial()["causal_windows"](
            np.array([1, 2, 3, 4, 5], dtype=np.float32),
            sample_rate=2,
            window_seconds=2,
            step_seconds=0.5,
            latency_seconds=1,
        )
    )
    assert [start for start, _ in windows] == [-1.5, -1, -0.5, 0, 0.5, 1]
    np.testing.assert_array_equal(windows[0][1], [0, 0, 0, 1])
    np.testing.assert_array_equal(windows[2][1], [0, 1, 2, 3])
    np.testing.assert_array_equal(windows[-1][1], [3, 4, 5, 0])


def test_padding_annotations_are_not_reported_as_real_speech():
    clip = trial()["clip_segments"]
    rows = [
        (-0.5, -0.1, "speaker0"),
        (-0.2, 0.3, "speaker1"),
        (1.8, 2.2, "speaker1"),
        (2.1, 2.4, "speaker0"),
    ]
    assert clip(rows, 2) == [(0.0, 0.3, "speaker1"), (1.8, 2, "speaker1")]


def test_upstream_startup_prepend_cannot_republish_old_speaker_assignments():
    publish = trial().get("publication_segments")
    assert publish is not None, "Diart output has no publication-window guard"
    rows = [(0, 5, "speaker1")]
    assert publish(rows, audio_seconds=8, available_at=5, latency=0.5) == [
        (4.5, 5, "speaker1")
    ]
    assert publish(rows, audio_seconds=8, available_at=5, latency=1) == [
        (4, 4.5, "speaker1")
    ]
