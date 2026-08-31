"""Durable atomic artifact and generation publication primitives."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from alice.experiments.manifest import ArtifactRecord


def sha256_path(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(path: Path, *, logical_path: str | None = None) -> ArtifactRecord:
    return ArtifactRecord(
        path=logical_path or path.name,
        sha256=sha256_path(path),
        size_bytes=path.stat().st_size,
    )


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write one file durably through an exclusive same-directory temp."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    os.fchmod(temp_fd, 0o600)
    try:
        with os.fdopen(temp_fd, "wb") as handle:
            temp_fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        fsync_directory(path.parent)
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        temp_path.unlink(missing_ok=True)


def publish_generation(
    generations_dir: Path,
    generation_id: str,
    files: Mapping[str, bytes],
) -> Path:
    """Publish a complete immutable directory with one atomic rename."""

    generations_dir.mkdir(parents=True, exist_ok=True)
    fsync_directory(generations_dir.parent)
    final_dir = generations_dir / generation_id
    if final_dir.exists():
        raise FileExistsError(f"analysis generation already exists: {generation_id}")
    stage_dir = Path(
        tempfile.mkdtemp(prefix=".stage-", dir=generations_dir)
    )
    try:
        for name, payload in sorted(files.items()):
            if Path(name).name != name:
                raise ValueError(f"generation artifact must be a filename: {name}")
            artifact_path = stage_dir / name
            descriptor = os.open(
                artifact_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        fsync_directory(stage_dir)
        os.replace(stage_dir, final_dir)
        fsync_directory(generations_dir)
        return final_dir
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
