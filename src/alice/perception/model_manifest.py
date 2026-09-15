"""Single source of truth for the pinned MediaPipe model identity."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from alice.contracts.blendshapes import NonEmptyString, Sha256Hex


class ModelManifest(BaseModel):
    """Validated identity and provenance for the pinned task bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["mediapipe-model-manifest/v1"]
    model_id: Literal["mediapipe-face-landmarker-v1"]
    model_asset_name: NonEmptyString
    source_url: NonEmptyString
    published_model_identity: NonEmptyString
    sha256: Sha256Hex
    retrieved_at: date
    permitted_use_reference: NonEmptyString


PINNED_MANIFEST = ModelManifest.model_validate(
    {
        "schema_version": "mediapipe-model-manifest/v1",
        "model_id": "mediapipe-face-landmarker-v1",
        "model_asset_name": "face_landmarker.task",
        "source_url": (
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/latest/face_landmarker.task"
        ),
        "published_model_identity": (
            "MediaPipe Face Landmarker float16 latest "
            "(storage generation 1683136941468629, "
            "last_modified 2023-05-03T18:02:21Z)"
        ),
        "sha256": "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
        "retrieved_at": "2026-08-31",
        "permitted_use_reference": "https://ai.google.dev/edge/mediapipe/solutions/guide",
    }
)


def sha256_path(path: Path) -> str:
    """Hash a model before any parser receives its path."""

    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_model_artifact(
    model_path: Path,
) -> str:
    """Return the hash only when the artifact matches the pinned identity."""

    observed_sha256 = sha256_path(model_path)
    if observed_sha256 != PINNED_MANIFEST.sha256:
        raise ValueError(
            "pinned model SHA-256 mismatch: "
            f"expected {PINNED_MANIFEST.sha256} but observed {observed_sha256}"
        )
    return observed_sha256


def load_model_manifest(path: Path) -> ModelManifest:
    """Load a model manifest without weakening the caller's pin policy."""

    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return ModelManifest.model_validate(payload)


def require_pinned_manifest(
    manifest: ModelManifest,
    *,
    pinned_manifest: ModelManifest = PINNED_MANIFEST,
) -> ModelManifest:
    """Reject any alteration to any pinned identity or provenance field."""

    if manifest != pinned_manifest:
        raise ValueError("tracked manifest does not match pinned provenance")
    return manifest
