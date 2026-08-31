"""Typed configuration for passive observation runs."""

from __future__ import annotations

from datetime import timedelta
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

    camera_id: NonEmptyString
    stable_camera_identity: NonEmptyString
    alice_full_face_confirmed: Literal[True]
    participant_exclusion_confirmed: Literal[True]
    confirmation: SetupConfirmation
    lighting: CameraCondition
    placement: CameraCondition
    focus: CameraCondition
    exposure: CameraCondition

    @model_validator(mode="after")
    def reject_unreviewed_placeholders(self) -> CaptureSetup:
        values = (
            self.camera_id,
            self.stable_camera_identity,
            self.confirmation.source,
            self.lighting.detail,
            self.placement.detail,
            self.focus.detail,
            self.exposure.detail,
        )
        if any(value.upper().startswith("REQUIRED:") for value in values):
            raise ValueError("setup contains an unreviewed REQUIRED placeholder")
        return self


class RawRetentionApproval(BaseModel):
    """Structured approval required before raw image retention is enabled."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: NonEmptyString
    scope: NonEmptyString
    approval_source: NonEmptyString
    consent_provenance: NonEmptyString
    approved_at: AwareDatetime
    expires_at: AwareDatetime
    retention_duration_days: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_approval_window(self) -> RawRetentionApproval:
        if self.expires_at <= self.approved_at:
            raise ValueError("raw retention approval must expire after approval time")
        if self.expires_at - self.approved_at < timedelta(
            days=self.retention_duration_days
        ):
            raise ValueError(
                "raw retention approval window is shorter than approved duration"
            )
        return self


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
        if (
            self.raw_approval is not None
            and self.retention_duration_days
            > self.raw_approval.retention_duration_days
        ):
            raise ValueError(
                "retention duration exceeds approved retention duration"
            )
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
