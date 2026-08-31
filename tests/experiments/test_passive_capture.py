import json
from datetime import UTC, datetime, timedelta
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
            observed_at=frame.captured_at + timedelta(milliseconds=5),
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
        "maximum_observation_age_ms": 250,
        "retain_frames": False,
        "retention_policy": {
            "policy_id": "derived-observations-365d",
            "mode": "derived_observations_only",
            "retention_duration_days": 365,
        },
        "setup": {
            "camera_id": "alice-face-webcam",
            "stable_camera_identity": "usb-Alice-video-index0",
            "alice_full_face_confirmed": True,
            "participant_exclusion_confirmed": True,
            "confirmation": {
                "confirmed_at": "2026-08-31T09:00:00Z",
                "source": "operator preview",
            },
            "lighting": {"state": "confirmed", "detail": "lab lights"},
            "placement": {"state": "confirmed", "detail": "tripod"},
            "focus": {"state": "unknown", "detail": "not measurable"},
            "exposure": {"state": "unknown", "detail": "not measurable"},
        },
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
            retention_policy={
                "policy_id": "raw-policy",
                "mode": "raw_frames",
                "retention_duration_days": 30,
            },
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
        retention_policy={
            "policy_id": "raw-policy",
            "mode": "raw_frames",
            "retention_duration_days": 30,
                "raw_approval": {
                    "approval_id": "privacy-approval-001",
                    "scope": "Alice robot-face calibration frames only",
                    "approval_source": "reviewed test procedure",
                    "consent_provenance": "robot owner; no participant present",
                    "approved_at": "2026-08-30T00:00:00Z",
                    "expires_at": "2026-09-30T00:00:00Z",
                    "retention_duration_days": 30,
                },
        },
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
    assert payload["aborted_reason"] == "capture aborted; see failure metadata"
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
    assert payload["aborted_reason"] == "capture aborted; see failure metadata"
    assert payload["conclusion"] is None
    assert payload["failure"]["category"] == "observer_error"
    assert payload["failure"]["error_type"] == "RuntimeError"


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
    assert payload["aborted_reason"] == "capture aborted; see failure metadata"
    assert payload["failure"]["category"] == "observation_identity_mismatch"
    if message == "camera_id":
        assert payload["failure"]["error_type"] == "ValueError"
    else:
        assert payload["failure"]["error_type"] == "ValueError"


def test_failure_manifest_omits_exception_text_and_secrets(
    tmp_path: Path,
    frame_source: FakeFrameSource,
) -> None:
    secret_token = "TOKEN-abc123-secret"
    secret_path = "/tmp/private/secret-file.txt"

    class SecretObserver(FakeObserver):
        def observe(self, frame: CapturedFrame, run_id: str) -> BlendshapeObservation:
            raise RuntimeError(
                f"observer failed using {secret_path} and bearer {secret_token}"
            )

    with pytest.raises(RuntimeError, match=secret_token):
        run_passive_capture(
            capture_config(sample_count=1, duration_seconds=1),
            frame_source,
            SecretObserver(),
            tmp_path,
        )

    manifest_text = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    assert secret_token not in manifest_text
    assert secret_path not in manifest_text
    payload = json.loads(manifest_text)
    assert payload["aborted_reason"] == "capture aborted; see failure metadata"
    assert payload["failure"]["category"] == "observer_error"
    assert payload["failure"]["error_type"] == "RuntimeError"
    assert "message" not in payload["failure"]


def test_capture_sleeps_to_deadlines_instead_of_interval_plus_processing(
    tmp_path: Path,
    frame_source: FakeFrameSource,
) -> None:
    monotonic_values = iter(
        [
            0,
            20_000_000,
            120_000_000,
        ]
    )
    sleep_calls: list[float] = []

    manifest = run_passive_capture(
        capture_config(sample_count=3, sample_interval_ms=100, duration_seconds=1),
        frame_source,
        FakeObserver(),
        tmp_path,
        monotonic_ns=lambda: next(monotonic_values),
        sleep=sleep_calls.append,
    )

    assert manifest.status == "completed"
    assert sleep_calls == [0.08, 0.08]


def test_capture_requires_typed_setup_and_privacy_confirmation() -> None:
    with pytest.raises(ValueError, match="setup"):
        PassiveCaptureConfig.model_validate(
            {
                "run_id": "passive-001",
                "camera_id": "alice-face-webcam",
                "requested_width": 640,
                "requested_height": 480,
                "requested_fps": 10,
                "duration_seconds": 1,
                "sample_count": 1,
                "sample_interval_ms": 0,
                "maximum_observation_age_ms": 250,
                "retain_frames": False,
                "retention_policy": {
                    "policy_id": "derived-only",
                    "mode": "derived_observations_only",
                    "retention_duration_days": 365,
                },
            }
        )


def test_capture_rejects_future_setup_confirmation_before_camera_read(
    tmp_path: Path,
    frame_source: FakeFrameSource,
) -> None:
    config = capture_config(
        sample_count=1,
        setup={
            **capture_config().setup.model_dump(mode="json"),
            "confirmation": {
                "confirmed_at": "2026-09-01T00:00:00Z",
                "source": "operator preview",
            },
        },
    )

    with pytest.raises(ValueError, match="confirmation.*future"):
        run_passive_capture(
            config,
            frame_source,
            FakeObserver(),
            tmp_path,
            utc_now=lambda: datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
        )

    assert frame_source._index == 0


def test_capture_rejects_setup_camera_identity_mismatch_before_camera_read(
    tmp_path: Path,
    frame_source: FakeFrameSource,
) -> None:
    config = capture_config(
        sample_count=1,
        setup={
            **capture_config().setup.model_dump(mode="json"),
            "camera_id": "different-camera",
        },
    )

    with pytest.raises(ValueError, match="setup camera_id"):
        run_passive_capture(
            config,
            frame_source,
            FakeObserver(),
            tmp_path,
            utc_now=lambda: datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
        )

    assert frame_source._index == 0


def test_config_rejects_overlong_raw_retention_approval() -> None:
    raw_approval = {
        "approval_id": "approval-001",
        "scope": "Alice calibration frames only",
        "approval_source": "operator-reviewed procedure",
        "consent_provenance": "robot owner authorization; no participant present",
        "approved_at": "2026-08-30T00:00:00Z",
        "expires_at": "2026-09-30T00:00:00Z",
        "retention_duration_days": 1,
    }

    with pytest.raises(ValueError, match="retention duration"):
        capture_config(
            sample_count=1,
            retain_frames=True,
            retention_policy={
                "policy_id": "raw-approved",
                "mode": "raw_frames",
                "retention_duration_days": 2,
                "raw_approval": raw_approval,
            },
        )


def test_config_rejects_approval_duration_beyond_expiry_window() -> None:
    with pytest.raises(ValueError, match="window is shorter"):
        capture_config(
            sample_count=1,
            retain_frames=True,
            retention_policy={
                "policy_id": "raw-approved",
                "mode": "raw_frames",
                "retention_duration_days": 2,
                "raw_approval": {
                    "approval_id": "approval-001",
                    "scope": "Alice calibration frames only",
                    "approval_source": "operator-reviewed procedure",
                    "consent_provenance": (
                        "robot owner authorization; no participant present"
                    ),
                    "approved_at": "2026-08-30T12:00:00Z",
                    "expires_at": "2026-09-01T00:00:00Z",
                    "retention_duration_days": 2,
                },
            },
        )


def test_capture_rejects_expired_raw_retention_approval(
    tmp_path: Path,
    frame_source: FakeFrameSource,
) -> None:
    raw_approval = {
        "approval_id": "approval-001",
        "scope": "Alice calibration frames only",
        "approval_source": "operator-reviewed procedure",
        "consent_provenance": "robot owner authorization; no participant present",
        "approved_at": "2026-08-30T00:00:00Z",
        "expires_at": "2026-08-31T00:00:00Z",
        "retention_duration_days": 1,
    }
    config = capture_config(
        sample_count=1,
        retain_frames=True,
        retention_policy={
            "policy_id": "raw-approved",
            "mode": "raw_frames",
            "retention_duration_days": 1,
            "raw_approval": raw_approval,
        },
    )

    with pytest.raises(ValueError, match="approval expired"):
        run_passive_capture(
            config,
            frame_source,
            FakeObserver(),
            tmp_path,
            utc_now=lambda: datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
        )

    assert frame_source._index == 0

    with pytest.raises(ValueError, match="alice_full_face_confirmed"):
        capture_config(
            setup={
                **capture_config().setup.model_dump(mode="json"),
                "alice_full_face_confirmed": False,
            }
        )


@pytest.mark.parametrize(
    ("captured_times", "monotonic_values", "message"),
    [
        ([0, 0], [100, 101], "captured_at"),
        ([0, 1], [100, 100], "monotonic_ns"),
        ([1, 0], [100, 101], "captured_at"),
        ([0, 1], [101, 100], "monotonic_ns"),
    ],
)
def test_capture_aborts_on_duplicate_or_decreasing_observation_timestamps(
    tmp_path: Path,
    captured_times: list[int],
    monotonic_values: list[int],
    message: str,
) -> None:
    frames = [
        CapturedFrame(
            captured_at=datetime(2026, 8, 31, 9, 0, second, tzinfo=UTC),
            monotonic_ns=monotonic,
            bgr=np.zeros((2, 3, 3), dtype=np.uint8),
        )
        for second, monotonic in zip(captured_times, monotonic_values, strict=True)
    ]

    with pytest.raises(ValueError, match=message):
        run_passive_capture(
            capture_config(sample_count=2, sample_interval_ms=0, duration_seconds=1),
            FakeFrameSource(frames),
            FakeObserver(),
            tmp_path,
        )

    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert payload["status"] == "aborted"
    assert payload["failure"]["category"] == "observation_sequence_invalid"


def test_capture_aborts_when_observation_exceeds_configured_maximum_age(
    tmp_path: Path,
) -> None:
    class StaleObserver(FakeObserver):
        def observe(self, frame: CapturedFrame, run_id: str) -> BlendshapeObservation:
            observation = super().observe(frame, run_id)
            return observation.model_copy(
                update={
                    "observed_at": frame.captured_at + timedelta(milliseconds=251)
                }
            )

    frames = [
        CapturedFrame(
            captured_at=datetime(2026, 8, 31, 9, 0, tzinfo=UTC),
            monotonic_ns=100,
            bgr=np.zeros((2, 3, 3), dtype=np.uint8),
        )
    ]
    with pytest.raises(ValueError, match="maximum_observation_age_ms"):
        run_passive_capture(
            capture_config(sample_count=1, sample_interval_ms=0, duration_seconds=1),
            FakeFrameSource(frames),
            StaleObserver(),
            tmp_path,
        )
