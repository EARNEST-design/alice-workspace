from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from alice.experiments.manifest import ArtifactManifest


def _manifest_payload() -> dict[str, object]:
    started = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    return {
        "schema_version": "artifact-manifest/v1",
        "run_id": "passive-001",
        "status": "completed",
        "started_at": started,
        "ended_at": started + timedelta(seconds=1),
        "observation_count": 1,
        "config": {},
        "artifacts": {
            "observations.jsonl": {
                "path": "observations.jsonl",
                "sha256": "a" * 64,
                "size_bytes": 1,
            }
        },
        "git_revision": "b" * 40,
        "dependency_lock_path": "uv.lock",
        "dependency_lock_sha256": "c" * 64,
        "python_version": "3.13.7",
        "platform_system": "Linux",
        "platform_release": "6.8.0",
        "platform_machine": "x86_64",
        "camera_settings": {
            name: {"availability": "unavailable", "value": None, "set_succeeded": None}
            for name in ("width", "height", "fps", "focus", "exposure")
        },
        "aborted_reason": None,
        "failure": None,
        "conclusion": None,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        {"ended_at": datetime(2026, 8, 31, 8, 59, tzinfo=UTC)},
        {"conclusion": "analysis mutated capture evidence"},
        {
            "artifacts": {
                "wrong-key": {
                    "path": "observations.jsonl",
                    "sha256": "a" * 64,
                    "size_bytes": 1,
                }
            }
        },
        {
            "artifacts": {
                "observations.jsonl": {
                    "path": "other.jsonl",
                    "sha256": "a" * 64,
                    "size_bytes": 1,
                }
            }
        },
    ],
)
def test_completed_manifest_rejects_incoherent_state(
    mutation: dict[str, object],
) -> None:
    payload = _manifest_payload()
    payload.update(mutation)

    with pytest.raises(ValidationError):
        ArtifactManifest.model_validate(payload)


def test_aborted_manifest_requires_failure_and_reason() -> None:
    payload = _manifest_payload()
    payload.update(status="aborted", artifacts={}, observation_count=0)

    with pytest.raises(ValidationError, match="failure"):
        ArtifactManifest.model_validate(payload)
