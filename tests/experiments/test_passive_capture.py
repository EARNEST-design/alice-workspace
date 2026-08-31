import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from alice.contracts import BlendshapeObservation, BlendshapeScore, ObservationValidity
from alice.perception.camera import CapturedFrame, FrameSource


class FakeFrameSource(FrameSource):
    def __init__(self, frames: list[CapturedFrame]) -> None:
        self._frames = frames
        self._index = 0

    def read(self) -> CapturedFrame:
        frame = self._frames[self._index]
        self._index += 1
        return frame


class FakeObserver:
    def __init__(self, *, interrupt_on_call: int | None = None) -> None:
        self._interrupt_on_call = interrupt_on_call
        self._call_count = 0

    def observe(self, frame: CapturedFrame, run_id: str) -> BlendshapeObservation:
        self._call_count += 1
        if self._interrupt_on_call == self._call_count:
            raise KeyboardInterrupt("stop capture")

        return BlendshapeObservation(
            schema_version="blendshape-observation/v1",
            captured_at=frame.captured_at,
            monotonic_ns=frame.monotonic_ns,
            camera_id="alice-face-webcam",
            run_id=run_id,
            detector="mediapipe-face-landmarker",
            detector_model_sha256="a" * 64,
            image_width=frame.bgr.shape[1],
            image_height=frame.bgr.shape[0],
            face_confidence=0.91,
            validity=ObservationValidity.VALID,
            invalid_reason=None,
            scores=(BlendshapeScore(name="jawOpen", score=0.25),),
        )


@pytest.fixture
def frame_source() -> FakeFrameSource:
    frames = [
        CapturedFrame(
            captured_at=datetime(2026, 8, 31, 9, 0, second, tzinfo=UTC),
            monotonic_ns=100 + second,
            bgr=np.full((2, 3, 3), fill_value=second, dtype=np.uint8),
        )
        for second in range(4)
    ]
    return FakeFrameSource(frames)


def test_capture_writes_manifest_observations_and_checksums(
    tmp_path: Path,
    frame_source: FakeFrameSource,
) -> None:
    from alice.experiments.passive_capture import (
        PassiveCaptureConfig,
        run_passive_capture,
    )

    config = PassiveCaptureConfig(
        run_id="passive-001",
        sample_count=3,
        sample_interval_ms=0,
        retain_frames=False,
    )

    manifest = run_passive_capture(config, frame_source, FakeObserver(), tmp_path)

    lines = (tmp_path / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert not list(tmp_path.glob("*.png"))
    assert manifest.status == "completed"
    assert manifest.artifacts["observations.jsonl"].sha256
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert payload["run_id"] == "passive-001"
    assert payload["status"] == "completed"


def test_capture_requires_retention_approval_when_frames_are_retained() -> None:
    from alice.experiments.passive_capture import PassiveCaptureConfig

    with pytest.raises(ValueError, match="retention_approval"):
        PassiveCaptureConfig(
            run_id="passive-001",
            sample_count=1,
            sample_interval_ms=0,
            retain_frames=True,
            retention_approval=" ",
        )


def test_capture_retains_frames_only_with_approval(
    tmp_path: Path,
    frame_source: FakeFrameSource,
) -> None:
    from alice.experiments.passive_capture import (
        PassiveCaptureConfig,
        run_passive_capture,
    )

    config = PassiveCaptureConfig(
        run_id="passive-001",
        sample_count=2,
        sample_interval_ms=0,
        retain_frames=True,
        retention_approval="privacy-approval-001",
    )

    manifest = run_passive_capture(config, frame_source, FakeObserver(), tmp_path)

    frame_files = sorted(tmp_path.glob("frame-*.png"))
    assert [path.name for path in frame_files] == [
        "frame-000001.png",
        "frame-000002.png",
    ]
    assert all(path.name in manifest.artifacts for path in frame_files)


def test_interrupted_run_writes_aborted_manifest_without_conclusion(
    tmp_path: Path,
    frame_source: FakeFrameSource,
) -> None:
    from alice.experiments.passive_capture import (
        PassiveCaptureConfig,
        run_passive_capture,
    )

    config = PassiveCaptureConfig(
        run_id="passive-001",
        sample_count=3,
        sample_interval_ms=0,
        retain_frames=False,
    )

    manifest = run_passive_capture(
        config,
        frame_source,
        FakeObserver(interrupt_on_call=2),
        tmp_path,
    )

    assert manifest.status == "aborted"
    assert manifest.conclusion is None
    assert not (tmp_path / "observations.jsonl").exists()
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert payload["status"] == "aborted"
    assert payload["conclusion"] is None
