"""Download and verify the pinned MediaPipe face-landmarker artifact."""

from __future__ import annotations

import argparse
import os
import tempfile
import urllib.request
from hashlib import sha256
from pathlib import Path
from typing import Callable, Protocol, TextIO

from pydantic import BaseModel, ConfigDict

from alice.contracts.blendshapes import Sha256Hex
from alice.perception.model_manifest import (
    PINNED_MANIFEST,
    ModelManifest,
    load_model_manifest,
    require_pinned_manifest,
)


class ResponseLike(Protocol):
    def __enter__(self) -> "ResponseLike": ...

    def __exit__(self, *_args: object) -> None: ...

    def read(self, size: int = -1) -> bytes: ...


class InstalledModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    sha256: Sha256Hex


ModelManifest.model_rebuild()
InstalledModel.model_rebuild()

TRACKED_MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent
    / "config/models/mediapipe-face-landmarker-v1.yaml"
).resolve()
def load_manifest(manifest_path: Path) -> ModelManifest:
    resolved_path = manifest_path.resolve()
    if resolved_path != TRACKED_MANIFEST_PATH:
        raise ValueError(
            f"manifest_path must be the tracked manifest: {TRACKED_MANIFEST_PATH}"
        )

    manifest = load_model_manifest(manifest_path)
    return require_pinned_manifest(manifest, pinned_manifest=PINNED_MANIFEST)


def install_model(
    *,
    manifest_path: Path,
    install_dir: Path,
    urlopen: Callable[[str], ResponseLike] = urllib.request.urlopen,
) -> InstalledModel:
    manifest = load_manifest(manifest_path)
    install_dir.mkdir(parents=True, exist_ok=True)
    final_path = install_dir / manifest.model_asset_name
    digest = sha256()
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{manifest.model_asset_name}.",
        suffix=".tmp",
        dir=install_dir,
    )
    temp_path = Path(temp_name)
    os.fchmod(temp_fd, 0o600)

    try:
        with (
            urlopen(manifest.source_url) as response,
            os.fdopen(temp_fd, "wb") as handle,
        ):
            temp_fd = -1
            while True:
                chunk = response.read(1024 * 1024)
                if chunk == b"":
                    break
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())

        observed_sha256 = digest.hexdigest()
        if observed_sha256 != manifest.sha256:
            raise ValueError(
                "SHA-256 mismatch: "
                f"expected {manifest.sha256} but downloaded {observed_sha256}"
            )

        os.replace(temp_path, final_path)
        _fsync_directory(install_dir)
        return InstalledModel(path=final_path, sha256=observed_sha256)
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        temp_path.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fetch_mediapipe_model")
    parser.add_argument("manifest_path", type=Path)
    parser.add_argument("--install-dir", type=Path, default=Path("models"))
    return parser


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    urlopen: Callable[[str], ResponseLike] = urllib.request.urlopen,
) -> int:
    args = build_parser().parse_args(argv)
    output = stdout if stdout is not None else None
    installed = install_model(
        manifest_path=args.manifest_path,
        install_dir=args.install_dir,
        urlopen=urlopen,
    )
    line = f"{installed.path} {installed.sha256}\n"
    if output is None:
        print(line, end="")
    else:
        output.write(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
