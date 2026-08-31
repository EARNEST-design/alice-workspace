"""Passive capture execution and immutable capture-manifest publication."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Protocol, TextIO, TypeVar

import cv2

from alice.contracts import BlendshapeObservation
from alice.experiments.artifact_store import (
    artifact_record,
    atomic_write_bytes,
    fsync_directory,
    sha256_path,
)
from alice.experiments.config import PassiveCaptureConfig
from alice.experiments.manifest import (
    ArtifactManifest,
    ArtifactRecord,
    FailureCategory,
    FailureRecord,
    NegotiatedCameraSettings,
    RunStatus,
)
from alice.perception import CapturedFrame, FrameSource

_REPO_ROOT = Path(__file__).resolve().parents[3]
_T = TypeVar("_T")
_ABORTED_REASON = "capture aborted; see failure metadata"


class BlendshapeObserver(Protocol):
    def observe(self, frame: CapturedFrame, run_id: str) -> BlendshapeObservation: ...


def run_passive_capture(
    config: PassiveCaptureConfig,
    frame_source: FrameSource,
    observer: BlendshapeObserver,
    output_dir: Path,
    *,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    sleep: Callable[[float], object] = time.sleep,
) -> ArtifactManifest:
    """Capture observations and publish one immutable capture manifest."""

    run_dir = _prepare_run_dir(output_dir)
    started_at = datetime.now(UTC)
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=".observations.", suffix=".jsonl.tmp", dir=run_dir
    )
    observations_tmp_path = Path(temp_name)
    os.fchmod(temp_fd, 0o600)
    artifacts: dict[str, ArtifactRecord] = {}
    observation_count = 0
    start_ns = monotonic_ns()
    previous_observation: BlendshapeObservation | None = None

    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as observations_handle:
            temp_fd = -1
            for sample_index in range(config.sample_count):
                frame = _run_stage(
                    stage="capture",
                    operation=frame_source.read,
                    config=config,
                    frame_source=frame_source,
                    run_dir=run_dir,
                    observations_tmp_path=observations_tmp_path,
                    started_at=started_at,
                    observation_count=observation_count,
                    artifacts=artifacts,
                )
                observation = _run_stage(
                    stage="observe",
                    operation=lambda: observer.observe(frame, config.run_id),
                    config=config,
                    frame_source=frame_source,
                    run_dir=run_dir,
                    observations_tmp_path=observations_tmp_path,
                    started_at=started_at,
                    observation_count=observation_count,
                    artifacts=artifacts,
                )
                _run_stage(
                    stage="validate_observation",
                    operation=lambda: _validate_observation_identity(
                        config=config,
                        observation=observation,
                    ),
                    config=config,
                    frame_source=frame_source,
                    run_dir=run_dir,
                    observations_tmp_path=observations_tmp_path,
                    started_at=started_at,
                    observation_count=observation_count,
                    artifacts=artifacts,
                )
                _run_stage(
                    stage="validate_observation_sequence",
                    operation=lambda: _validate_observation_sequence(
                        config=config,
                        frame=frame,
                        observation=observation,
                        previous=previous_observation,
                    ),
                    config=config,
                    frame_source=frame_source,
                    run_dir=run_dir,
                    observations_tmp_path=observations_tmp_path,
                    started_at=started_at,
                    observation_count=observation_count,
                    artifacts=artifacts,
                )
                observations_handle.write(
                    json.dumps(observation.model_dump(mode="json"), sort_keys=True)
                )
                observations_handle.write("\n")
                observation_count += 1
                previous_observation = observation
                if config.retain_frames:
                    frame_name = f"frame-{sample_index + 1:06d}.png"
                    artifacts[frame_name] = _run_stage(
                        stage="encode_frame",
                        operation=lambda: _write_frame_artifact(
                            run_dir / frame_name,
                            frame,
                        ),
                        config=config,
                        frame_source=frame_source,
                        run_dir=run_dir,
                        observations_tmp_path=observations_tmp_path,
                        started_at=started_at,
                        observation_count=observation_count,
                        artifacts=artifacts,
                    )
                if sample_index + 1 < config.sample_count:
                    deadline_ns = (
                        start_ns
                        + (sample_index + 1) * config.sample_interval_ms * 1_000_000
                    )
                    remaining_ns = deadline_ns - monotonic_ns()
                    if remaining_ns > 0:
                        sleep(remaining_ns / 1_000_000_000)
            _flush_file(observations_handle)

        observations_path = run_dir / "observations.jsonl"
        os.replace(observations_tmp_path, observations_path)
        fsync_directory(run_dir)
        artifacts[observations_path.name] = artifact_record(observations_path)
    except KeyboardInterrupt as error:
        observations_tmp_path.unlink(missing_ok=True)
        manifest = _build_manifest(
            config=config,
            status=RunStatus.ABORTED,
            started_at=started_at,
            ended_at=datetime.now(UTC),
            observation_count=observation_count,
            artifacts=artifacts,
            failure=_failure_record("interrupted", error),
            camera_settings=_camera_settings(frame_source),
        )
        _write_manifest(run_dir, manifest)
        return manifest
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        observations_tmp_path.unlink(missing_ok=True)

    manifest = _build_manifest(
        config=config,
        status=RunStatus.COMPLETED,
        started_at=started_at,
        ended_at=datetime.now(UTC),
        observation_count=observation_count,
        artifacts=artifacts,
        failure=None,
        camera_settings=_camera_settings(frame_source),
    )
    _write_manifest(run_dir, manifest)
    return manifest


def _prepare_run_dir(output_dir: Path) -> Path:
    run_dir = output_dir.resolve()
    if run_dir.exists():
        if not run_dir.is_dir():
            raise NotADirectoryError(f"output_dir is not a directory: {run_dir}")
        if any(run_dir.iterdir()):
            raise FileExistsError(f"output_dir must be new or empty: {run_dir}")
        return run_dir
    run_dir.mkdir(parents=True, exist_ok=False)
    fsync_directory(run_dir.parent)
    return run_dir


def _run_stage(
    *,
    stage: str,
    operation: Callable[[], _T],
    config: PassiveCaptureConfig,
    frame_source: FrameSource,
    run_dir: Path,
    observations_tmp_path: Path,
    started_at: datetime,
    observation_count: int,
    artifacts: dict[str, ArtifactRecord],
) -> _T:
    try:
        return operation()
    except Exception as error:
        observations_tmp_path.unlink(missing_ok=True)
        manifest = _build_manifest(
            config=config,
            status=RunStatus.ABORTED,
            started_at=started_at,
            ended_at=datetime.now(UTC),
            observation_count=observation_count,
            artifacts=artifacts,
            failure=_failure_record(stage, error),
            camera_settings=_camera_settings(frame_source),
        )
        _write_manifest(run_dir, manifest)
        raise


def _build_manifest(
    *,
    config: PassiveCaptureConfig,
    status: RunStatus,
    started_at: datetime,
    ended_at: datetime,
    observation_count: int,
    artifacts: dict[str, ArtifactRecord],
    failure: FailureRecord | None,
    camera_settings: NegotiatedCameraSettings,
) -> ArtifactManifest:
    return ArtifactManifest(
        schema_version="artifact-manifest/v1",
        run_id=config.run_id,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        observation_count=observation_count,
        config=config.model_dump(mode="json"),
        artifacts=artifacts,
        git_revision=_git_revision(),
        dependency_lock_path=_dependency_lock_path(),
        dependency_lock_sha256=_dependency_lock_sha256(),
        python_version=platform.python_version(),
        platform_system=platform.system() or "unknown",
        platform_release=platform.release() or "unknown",
        platform_machine=platform.machine() or "unknown",
        camera_settings=camera_settings,
        aborted_reason=_ABORTED_REASON if failure is not None else None,
        failure=failure,
        conclusion=None,
    )


def _validate_observation_identity(
    *, config: PassiveCaptureConfig, observation: BlendshapeObservation
) -> None:
    if observation.camera_id != config.camera_id:
        raise ValueError("observation camera_id does not match config camera_id")
    if observation.run_id != config.run_id:
        raise ValueError("observation run_id does not match config run_id")


def _validate_observation_sequence(
    *,
    config: PassiveCaptureConfig,
    frame: CapturedFrame,
    observation: BlendshapeObservation,
    previous: BlendshapeObservation | None,
) -> None:
    if observation.captured_at != frame.captured_at:
        raise ValueError("observation captured_at must equal captured frame timestamp")
    if observation.monotonic_ns != frame.monotonic_ns:
        raise ValueError("observation monotonic_ns must equal captured frame timestamp")
    if previous is not None:
        if observation.captured_at <= previous.captured_at:
            raise ValueError("observation captured_at must be strictly increasing")
        if observation.monotonic_ns <= previous.monotonic_ns:
            raise ValueError("observation monotonic_ns must be strictly increasing")
    if observation.observed_at is None:
        raise ValueError(
            "observed_at is required to enforce maximum_observation_age_ms"
        )
    age_ms = (observation.observed_at - observation.captured_at).total_seconds() * 1000
    if age_ms < 0 or age_ms > config.maximum_observation_age_ms:
        raise ValueError(
            "observation exceeds maximum_observation_age_ms: "
            f"{age_ms:.3f} > {config.maximum_observation_age_ms}"
        )


def _camera_settings(frame_source: FrameSource) -> NegotiatedCameraSettings:
    value = getattr(frame_source, "negotiated_settings", None)
    return (
        value
        if isinstance(value, NegotiatedCameraSettings)
        else NegotiatedCameraSettings.unavailable()
    )


def _write_frame_artifact(path: Path, frame: CapturedFrame) -> ArtifactRecord:
    success, encoded = cv2.imencode(".png", frame.bgr)
    if not success:
        raise RuntimeError(f"failed to encode frame for {path.name}")
    atomic_write_bytes(path, encoded.tobytes())
    return artifact_record(path)


def _write_manifest(run_dir: Path, manifest: ArtifactManifest) -> None:
    payload = (
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True).encode()
        + b"\n"
    )
    atomic_write_bytes(run_dir / "manifest.json", payload)


def _flush_file(handle: TextIO) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _failure_record(stage: str, error: BaseException) -> FailureRecord:
    return FailureRecord(
        category=_failure_category(stage),
        error_type=_safe_error_type(error),
    )


def _failure_category(stage: str) -> FailureCategory:
    mapping = {
        "interrupted": FailureCategory.INTERRUPTED,
        "capture": FailureCategory.CAPTURE_ERROR,
        "observe": FailureCategory.OBSERVER_ERROR,
        "validate_observation": FailureCategory.OBSERVATION_IDENTITY_MISMATCH,
        "validate_observation_sequence": FailureCategory.OBSERVATION_SEQUENCE_INVALID,
        "encode_frame": FailureCategory.FRAME_ENCODING_ERROR,
    }
    try:
        return mapping[stage]
    except KeyError as error:
        raise ValueError(f"unknown failure stage: {stage}") from error


def _safe_error_type(error: BaseException) -> str:
    name = error.__class__.__name__
    return name if name.replace("_", "").isalnum() else "Exception"


def _git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _dependency_lock_path() -> str | None:
    lock_path = _REPO_ROOT / "uv.lock"
    return "uv.lock" if lock_path.is_file() else None


def _dependency_lock_sha256() -> str | None:
    lock_path = _REPO_ROOT / "uv.lock"
    return sha256_path(lock_path) if lock_path.is_file() else None
