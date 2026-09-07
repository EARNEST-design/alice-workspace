"""Versioned provenance manifests for offline analysis generations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.experiments.manifest import ArtifactRecord


class AnalyzerIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    package_version: NonEmptyString
    git_revision: NonEmptyString | None


class AnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: NonEmptyString
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)


class AnalysisManifest(BaseModel):
    """Checksummed immutable provenance for one complete analysis generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["analysis-manifest/v1"]
    generation_id: NonEmptyString
    analysis_kind: Literal["stability", "repeatability"]
    generated_at: AwareDatetime
    analyzer: AnalyzerIdentity
    inputs: dict[NonEmptyString, AnalysisInput]
    thresholds: dict[str, Any] | None
    thresholds_sha256: Sha256Hex
    artifacts: dict[NonEmptyString, ArtifactRecord]
    outcome: Literal["pass", "fail", "inconclusive"]

    @model_validator(mode="after")
    def validate_artifact_keys(self) -> AnalysisManifest:
        for key, artifact in self.artifacts.items():
            if key != artifact.path:
                raise ValueError("analysis artifact key must equal artifact path")
        return self
