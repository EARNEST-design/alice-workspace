from pathlib import Path

import pytest

from alice.experiments.artifact_store import publish_generation


def test_generation_publication_is_all_or_nothing_on_rename_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alice.experiments import artifact_store

    real_replace = artifact_store.os.replace

    def fail_directory_replace(source: object, destination: object) -> None:
        if Path(destination).name == "generation-001":
            raise OSError("simulated publication crash")
        real_replace(source, destination)

    monkeypatch.setattr(artifact_store.os, "replace", fail_directory_replace)

    with pytest.raises(OSError, match="publication crash"):
        publish_generation(
            tmp_path,
            "generation-001",
            {"metrics.json": b"{}\n", "conclusion.md": b"complete\n"},
        )

    assert not (tmp_path / "generation-001").exists()
    assert list(tmp_path.glob(".stage-*")) == []


def test_generation_publication_rejects_existing_generation_without_mutation(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "generation-001"
    existing.mkdir()
    evidence = existing / "metrics.json"
    evidence.write_bytes(b"old\n")

    with pytest.raises(FileExistsError, match="generation"):
        publish_generation(
            tmp_path,
            "generation-001",
            {"metrics.json": b"new\n"},
        )

    assert evidence.read_bytes() == b"old\n"
