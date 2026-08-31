"""Experiment artifact manifest models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    model_validator,
)

from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.hardware.adapter import AdapterIdentity
from alice.safety.supervisor import (
    OperatorApproval,
    PreflightEvidence,
    SafetyLimits,
)


class RunStatus(StrEnum):
    COMPLETED = "completed"
    ABORTED = "aborted"


class RunKind(StrEnum):
    PASSIVE_CAPTURE = "passive_capture"
    ACTUATOR_IDENTIFICATION = "actuator_identification"


class FailureCategory(StrEnum):
    INTERRUPTED = "interrupted"
    CAPTURE_ERROR = "capture_error"
    OBSERVER_ERROR = "observer_error"
    OBSERVATION_IDENTITY_MISMATCH = "observation_identity_mismatch"
    OBSERVATION_SEQUENCE_INVALID = "observation_sequence_invalid"
    FRAME_ENCODING_ERROR = "frame_encoding_error"
    ADAPTER_IDENTITY_MISMATCH = "adapter_identity_mismatch"
    SAFETY_ERROR = "safety_error"
    CONTROLLER_ERROR = "controller_error"
    CAMERA_LOSS = "camera_loss"
    TIMEOUT = "timeout"
    VISUAL_ERROR = "visual_error"
    RECOVERY_ERROR = "recovery_error"


class ArtifactRecord(BaseModel):
    """A checksummed artifact produced by one experiment run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: NonEmptyString
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)


class SettingAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class CameraPropertySetting(BaseModel):
    """An actual negotiated camera property and requested-set outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    availability: SettingAvailability
    value: FiniteFloat | None
    set_succeeded: bool | None

    @model_validator(mode="after")
    def validate_value_availability(self) -> CameraPropertySetting:
        if self.availability is SettingAvailability.AVAILABLE and self.value is None:
            raise ValueError("available camera setting requires a value")
        if (
            self.availability is SettingAvailability.UNAVAILABLE
            and self.value is not None
        ):
            raise ValueError("unavailable camera setting must not have a value")
        return self


class NegotiatedCameraSettings(BaseModel):
    """Actual camera state recorded in every capture manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    width: CameraPropertySetting
    height: CameraPropertySetting
    fps: CameraPropertySetting
    focus: CameraPropertySetting
    exposure: CameraPropertySetting

    @classmethod
    def unavailable(cls) -> NegotiatedCameraSettings:
        unavailable = CameraPropertySetting(
            availability=SettingAvailability.UNAVAILABLE,
            value=None,
            set_succeeded=None,
        )
        return cls(
            width=unavailable,
            height=unavailable,
            fps=unavailable,
            focus=unavailable,
            exposure=unavailable,
        )


class IdentificationObserverProvenance(BaseModel):
    """Actual perception identity and camera state declared for Phase 2."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    camera_id: NonEmptyString
    detector: NonEmptyString
    detector_model_sha256: Sha256Hex
    camera_settings: NegotiatedCameraSettings


class IdentificationRunMetadata(BaseModel):
    """Typed safety and provenance snapshot for an actuator-identification run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter_identity: AdapterIdentity
    observer: IdentificationObserverProvenance | None
    expected_observer: IdentificationObserverProvenance
    safety_limits: SafetyLimits
    preflight: PreflightEvidence | None
    approval: OperatorApproval | None
    hardware_manifest_path: NonEmptyString
    hardware_manifest_sha256: Sha256Hex
    hardware_manifest_canonical_sha256: Sha256Hex
    calibration_sha256: Sha256Hex
    config_sha256: Sha256Hex


class FailureRecord(BaseModel):
    """Sanitized structured failure details for aborted runs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: FailureCategory
    error_type: NonEmptyString


class ArtifactManifest(BaseModel):
    """Immutable metadata describing one passive capture run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["artifact-manifest/v1"]
    run_kind: RunKind = RunKind.PASSIVE_CAPTURE
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
    # Added after the first v1 pilot. The default preserves read compatibility
    # while new writers always provide measured or explicitly unavailable values.
    camera_settings: NegotiatedCameraSettings = Field(
        default_factory=NegotiatedCameraSettings.unavailable
    )
    aborted_reason: NonEmptyString | None = None
    failure: FailureRecord | None = None
    conclusion: NonEmptyString | None = None
    identification_metadata: IdentificationRunMetadata | None = None

    @model_validator(mode="after")
    def validate_manifest_state(self) -> ArtifactManifest:
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        for key, artifact in self.artifacts.items():
            if key != artifact.path:
                raise ValueError("artifact key must equal artifact path")
        if self.conclusion is not None:
            raise ValueError("capture manifest conclusion must remain unset")
        if self.run_kind is RunKind.ACTUATOR_IDENTIFICATION:
            if self.identification_metadata is None:
                raise ValueError(
                    "actuator identification manifest requires typed metadata"
                )
        elif self.identification_metadata is not None:
            raise ValueError(
                "passive capture manifest cannot contain identification metadata"
            )

        if self.status is RunStatus.COMPLETED:
            if self.failure is not None or self.aborted_reason is not None:
                raise ValueError("completed manifest must not contain failure state")
            observation_artifact = self.artifacts.get("observations.jsonl")
            if observation_artifact is None:
                raise ValueError("completed manifest requires observations.jsonl")
            if observation_artifact.path != "observations.jsonl":
                raise ValueError(
                    "observations artifact path must be observations.jsonl"
                )
        elif self.failure is None or self.aborted_reason is None:
            raise ValueError("aborted manifest requires failure and aborted_reason")
        return self
