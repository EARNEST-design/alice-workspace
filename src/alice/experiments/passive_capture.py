"""Reproducible passive blendshape capture runs."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Protocol, TextIO

import cv2
from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts import BlendshapeObservation
from alice.contracts.blendshapes import NonEmptyString
from alice.experiments.manifest import ArtifactManifest, ArtifactRecord, RunStatus
from alice.perception import CapturedFrame, FrameSource

_REPO_ROOT = Path(__file__).resolve().parents[3]


class BlendshapeObserver(Protocol):
    """Convert frames into stored passive observations."""

    def observe(self, frame: CapturedFrame, run_id: str) -> BlendshapeObservation:
        """Return one immutable observation for the captured frame."""


class PassiveCaptureConfig(BaseModel):
    """Configuration for one passive capture run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: NonEmptyString
    camera_id: NonEmptyString | None = None
    sample_count: int = Field(gt=0)
    sample_interval_ms: int = Field(ge=0)
    retain_frames: bool = False
    retention_approval: NonEmptyString | None = None
    operator_metadata: dict[str, NonEmptyString] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_frame_retention(self) -> "PassiveCaptureConfig":
        if self.retain_frames and self.retention_approval is None:
            raise ValueError(
                "retention_approval is required when retain_frames is true"
            )
        return self


def run_passive_capture(
    config: PassiveCaptureConfig,
    frame_source: FrameSource,
    observer: BlendshapeObserver,
    output_dir: Path,
) -> ArtifactManifest:
    """Capture a reproducible sequence of passive blendshape observations."""

    run_dir = output_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(UTC)
    observations_tmp_path = run_dir / ".observations.jsonl.tmp"
    artifacts: dict[str, ArtifactRecord] = {}
    observation_count = 0
    status = RunStatus.COMPLETED
    aborted_reason: str | None = None

    try:
        with observations_tmp_path.open("w", encoding="utf-8") as observations_handle:
            for sample_index in range(config.sample_count):
                frame = frame_source.read()
                observation = observer.observe(frame, config.run_id)
                observations_handle.write(
                    json.dumps(observation.model_dump(mode="json"), sort_keys=True)
                )
                observations_handle.write("\n")
                observation_count += 1

                if config.retain_frames:
                    frame_name = f"frame-{sample_index + 1:06d}.png"
                    artifacts[frame_name] = _write_frame_artifact(
                        run_dir / frame_name,
                        frame,
                    )

                if sample_index + 1 < config.sample_count:
                    time.sleep(config.sample_interval_ms / 1000.0)

            _flush_file(observations_handle)

        observations_path = run_dir / "observations.jsonl"
        observations_tmp_path.replace(observations_path)
        artifacts[observations_path.name] = _artifact_record(observations_path)
    except KeyboardInterrupt as error:
        status = RunStatus.ABORTED
        aborted_reason = str(error).strip() or "interrupted"
        observations_tmp_path.unlink(missing_ok=True)
    finally:
        ended_at = datetime.now(UTC)

    manifest = ArtifactManifest(
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
        aborted_reason=aborted_reason,
        conclusion=None,
    )
    _write_json_atomic(run_dir / "manifest.json", manifest.model_dump(mode="json"))
    return manifest


def _write_frame_artifact(path: Path, frame: CapturedFrame) -> ArtifactRecord:
    success, encoded = cv2.imencode(".png", frame.bgr)
    if not success:
        raise RuntimeError(f"failed to encode frame for {path.name}")
    _write_bytes_atomic(path, encoded.tobytes())
    return _artifact_record(path)


def _write_json_atomic(path: Path, payload: object) -> None:
    _write_bytes_atomic(
        path,
        json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n",
    )


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    temp_path.replace(path)


def _flush_file(handle: TextIO) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _artifact_record(path: Path) -> ArtifactRecord:
    return ArtifactRecord(
        path=path.name,
        sha256=_sha256_path(path),
        size_bytes=path.stat().st_size,
    )


def _sha256_path(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    revision = result.stdout.strip()
    return revision or None


def _dependency_lock_path() -> str | None:
    lock_path = _REPO_ROOT / "uv.lock"
    if not lock_path.is_file():
        return None
    return str(lock_path.relative_to(_REPO_ROOT))


def _dependency_lock_sha256() -> str | None:
    lock_path = _REPO_ROOT / "uv.lock"
    if not lock_path.is_file():
        return None
    return _sha256_path(lock_path)
