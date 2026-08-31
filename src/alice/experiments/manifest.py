"""Experiment artifact manifest models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from alice.contracts.blendshapes import NonEmptyString, Sha256Hex


class RunStatus(StrEnum):
    COMPLETED = "completed"
    ABORTED = "aborted"


class FailureCategory(StrEnum):
    INTERRUPTED = "interrupted"
    CAPTURE_ERROR = "capture_error"
    OBSERVER_ERROR = "observer_error"
    OBSERVATION_IDENTITY_MISMATCH = "observation_identity_mismatch"
    FRAME_ENCODING_ERROR = "frame_encoding_error"


class ArtifactRecord(BaseModel):
    """A checksummed artifact produced by one experiment run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: NonEmptyString
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)


class FailureRecord(BaseModel):
    """Sanitized structured failure details for aborted runs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: FailureCategory
    error_type: NonEmptyString


class ArtifactManifest(BaseModel):
    """Immutable metadata describing one passive capture run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["artifact-manifest/v1"]
    run_id: NonEmptyString
    status: RunStatus
    started_at: AwareDatetime
    ended_at: AwareDatetime
    observation_count: int = Field(ge=0)
    config: dict[str, Any]
    artifacts: dict[str, ArtifactRecord]
    git_revision: NonEmptyString | None
    dependency_lock_path: NonEmptyString | None
    dependency_lock_sha256: Sha256Hex | None
    python_version: NonEmptyString
    platform_system: NonEmptyString
    platform_release: NonEmptyString
    platform_machine: NonEmptyString
    aborted_reason: NonEmptyString | None = None
    failure: FailureRecord | None = None
    conclusion: NonEmptyString | None = None
