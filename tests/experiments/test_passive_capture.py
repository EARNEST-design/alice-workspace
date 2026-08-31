import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from alice.contracts import BlendshapeObservation, BlendshapeScore, ObservationValidity
from alice.experiments.passive_capture import PassiveCaptureConfig, run_passive_capture
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
    def __init__(
        self,
        *,
        interrupt_on_call: int | None = None,
        failure_on_call: int | None = None,
        camera_id: str = "alice-face-webcam",
        run_id: str | None = None,
    ) -> None:
        self._interrupt_on_call = interrupt_on_call
        self._failure_on_call = failure_on_call
        self._camera_id = camera_id
        self._run_id = run_id
        self._call_count = 0

    def observe(self, frame: CapturedFrame, run_id: str) -> BlendshapeObservation:
        self._call_count += 1
        if self._interrupt_on_call == self._call_count:
            raise KeyboardInterrupt("stop capture")
        if self._failure_on_call == self._call_count:
            raise RuntimeError("observer failed\nwith detail")

        return BlendshapeObservation(
            schema_version="blendshape-observation/v1",
            captured_at=frame.captured_at,
            monotonic_ns=frame.monotonic_ns,
            camera_id=self._camera_id,
            run_id=self._run_id or run_id,
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


def capture_config(**overrides: object) -> PassiveCaptureConfig:
    base = {
        "run_id": "passive-001",
        "camera_id": "alice-face-webcam",
        "requested_width": 640,
        "requested_height": 480,
        "requested_fps": 10,
        "duration_seconds": 120,
        "sample_count": 1200,
        "sample_interval_ms": 100,
        "retain_frames": False,
    }
    base.update(overrides)
    return PassiveCaptureConfig(**base)


def test_capture_writes_manifest_observations_and_checksums(
    tmp_path: Path,
    frame_source: FakeFrameSource,
) -> None:
    config = capture_config(
        sample_count=3,
        sample_interval_ms=0,
        duration_seconds=1,
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
    assert payload["config"]["requested_width"] == 640
    assert payload["config"]["requested_height"] == 480
    assert payload["config"]["requested_fps"] == 10
    assert payload["config"]["duration_seconds"] == 1


def test_capture_requires_retention_approval_when_frames_are_retained() -> None:
    with pytest.raises(ValueError, match="retention_approval"):
        capture_config(
            sample_count=1,
            sample_interval_ms=1000,
            duration_seconds=1,
            retain_frames=True,
            retention_approval=" ",
        )


def test_capture_requires_consistent_positive_request_values() -> None:
    with pytest.raises(ValueError, match="requested_width"):
        capture_config(requested_width=0)

    with pytest.raises(ValueError, match="requested_fps"):
        capture_config(requested_fps=0)


def test_capture_retains_frames_only_with_approval(
    tmp_path: Path,
    frame_source: FakeFrameSource,
) -> None:
    config = capture_config(
        sample_count=2,
        sample_interval_ms=0,
        duration_seconds=1,
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
    config = capture_config(
        sample_count=3,
        sample_interval_ms=0,
        duration_seconds=1,
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


def test_capture_rejects_non_empty_output_dir_without_modifying_existing_artifacts(
    tmp_path: Path,
    frame_source: FakeFrameSource,
) -> None:
    stale_frame = tmp_path / "frame-000001.png"
    stale_frame.write_bytes(b"stale-frame")

    with pytest.raises(FileExistsError, match="output_dir"):
        run_passive_capture(
            capture_config(sample_count=1, duration_seconds=1),
            frame_source,
            FakeObserver(),
            tmp_path,
        )

    assert stale_frame.read_bytes() == b"stale-frame"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["frame-000001.png"]


def test_operational_failure_writes_aborted_manifest_then_reraises(
    tmp_path: Path,
    frame_source: FakeFrameSource,
) -> None:
    with pytest.raises(RuntimeError, match="observer failed"):
        run_passive_capture(
            capture_config(sample_count=1, duration_seconds=1),
            frame_source,
            FakeObserver(failure_on_call=1),
            tmp_path,
        )

    assert not (tmp_path / ".observations.jsonl.tmp").exists()
    assert not (tmp_path / "observations.jsonl").exists()
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert payload["status"] == "aborted"
    assert payload["conclusion"] is None
    assert payload["failure"]["stage"] == "observe"
    assert payload["failure"]["error_type"] == "RuntimeError"
    assert payload["failure"]["message"] == "observer failed with detail"


@pytest.mark.parametrize(
    ("observer", "message"),
    [
        (FakeObserver(camera_id="other-camera"), "camera_id"),
        (FakeObserver(run_id="other-run"), "run_id"),
    ],
)
def test_capture_aborts_and_reraises_on_observation_identity_mismatch(
    tmp_path: Path,
    frame_source: FakeFrameSource,
    observer: FakeObserver,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        run_passive_capture(
            capture_config(sample_count=1, duration_seconds=1),
            frame_source,
            observer,
            tmp_path,
        )

    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert payload["status"] == "aborted"
    assert payload["failure"]["stage"] == "validate_observation"
