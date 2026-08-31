"""Experiment capture and artifact utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from alice.experiments.manifest import (
        ArtifactManifest,
        ArtifactRecord,
        FailureCategory,
        FailureRecord,
        RunStatus,
    )
    from alice.experiments.passive_capture import (
        PassiveCaptureConfig,
        run_passive_capture,
    )

__all__ = [
    "ArtifactManifest",
    "ArtifactRecord",
    "FailureCategory",
    "FailureRecord",
    "PassiveCaptureConfig",
    "RunStatus",
    "run_passive_capture",
]


def __getattr__(name: str) -> Any:
    if name in {
        "ArtifactManifest",
        "ArtifactRecord",
        "FailureCategory",
        "FailureRecord",
        "RunStatus",
    }:
        from alice.experiments import manifest as manifest_module

        return getattr(manifest_module, name)
    if name in {"PassiveCaptureConfig", "run_passive_capture"}:
        from alice.experiments import passive_capture as passive_capture_module

        return getattr(passive_capture_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
