"""Typed configuration for passive observation runs."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from alice.analysis.blendshape_stability import AcceptanceThresholds
from alice.analysis.repeatability import RepeatabilityThresholds
from alice.contracts.blendshapes import NonEmptyString


class CameraConditionState(StrEnum):
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


class CameraCondition(BaseModel):
    """One structured camera/setup condition used for sensitivity grouping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: CameraConditionState
    detail: NonEmptyString


class SetupConfirmation(BaseModel):
    """Operator confirmation provenance for the privacy/framing gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    confirmed_at: AwareDatetime
    source: NonEmptyString


class CaptureSetup(BaseModel):
    """Required typed setup and privacy evidence for a capture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_camera_identity: NonEmptyString
    alice_full_face_confirmed: Literal[True]
    participant_exclusion_confirmed: Literal[True]
    confirmation: SetupConfirmation
    lighting: CameraCondition
    placement: CameraCondition
    focus: CameraCondition
    exposure: CameraCondition


class RawRetentionApproval(BaseModel):
    """Structured approval required before raw image retention is enabled."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: NonEmptyString
    scope: NonEmptyString
    expires_at: AwareDatetime
    retention_duration_days: int = Field(gt=0)


class RetentionPolicy(BaseModel):
    """Explicit retention policy for derived-only or approved raw evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: NonEmptyString
    mode: Literal["derived_observations_only", "raw_frames"]
    retention_duration_days: int = Field(gt=0)
    raw_approval: RawRetentionApproval | None = None

    @model_validator(mode="after")
    def validate_raw_approval(self) -> RetentionPolicy:
        if self.mode == "raw_frames" and self.raw_approval is None:
            raise ValueError("retention_approval is required for raw_frames policy")
        if self.mode == "derived_observations_only" and self.raw_approval is not None:
            raise ValueError("raw_approval is not allowed for derived-only retention")
        return self


class PassiveCaptureConfig(BaseModel):
    """Configuration for one passive capture run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: NonEmptyString
    camera_id: NonEmptyString
    camera_device: NonEmptyString | None = None
    requested_width: int = Field(gt=0)
    requested_height: int = Field(gt=0)
    requested_fps: int = Field(gt=0)
    duration_seconds: int = Field(gt=0)
    sample_count: int = Field(gt=0)
    sample_interval_ms: int = Field(ge=0)
    maximum_observation_age_ms: int = Field(gt=0)
    retain_frames: bool = False
    retention_policy: RetentionPolicy
    setup: CaptureSetup
    acceptance_thresholds: AcceptanceThresholds | None = None
    repeatability_thresholds: RepeatabilityThresholds | None = None

    @model_validator(mode="after")
    def validate_frame_retention(self) -> PassiveCaptureConfig:
        raw_policy = self.retention_policy.mode == "raw_frames"
        if self.retain_frames != raw_policy:
            raise ValueError(
                "retain_frames must match retention_policy.mode; raw retention "
                "requires structured retention_approval"
            )
        return self
