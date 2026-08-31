"""Download and verify the pinned MediaPipe face-landmarker artifact."""

from __future__ import annotations

import argparse
import os
import urllib.request
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Callable, Literal, Protocol, TextIO

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, StringConstraints

Sha256Hex = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ResponseLike(Protocol):
    def __enter__(self) -> "ResponseLike": ...

    def __exit__(self, *_args: object) -> None: ...

    def read(self, size: int = -1) -> bytes: ...


class ModelManifest(BaseModel):
    """Validated manifest for the pinned MediaPipe task bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["mediapipe-model-manifest/v1"]
    model_id: Literal["mediapipe-face-landmarker-v1"]
    model_asset_name: NonEmptyString
    source_url: NonEmptyString
    published_model_identity: NonEmptyString
    sha256: Sha256Hex
    retrieved_at: date
    permitted_use_reference: NonEmptyString


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


def load_manifest(manifest_path: Path) -> ModelManifest:
    resolved_path = manifest_path.resolve()
    if resolved_path != TRACKED_MANIFEST_PATH:
        raise ValueError(
            f"manifest_path must be the tracked manifest: {TRACKED_MANIFEST_PATH}"
        )

    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    manifest = ModelManifest.model_validate(payload)
    if manifest != PINNED_MANIFEST:
        raise ValueError("tracked manifest does not match pinned provenance")
    return manifest


def install_model(
    *,
    manifest_path: Path,
    install_dir: Path,
    urlopen: Callable[[str], ResponseLike] = urllib.request.urlopen,
) -> InstalledModel:
    manifest = load_manifest(manifest_path)
    install_dir.mkdir(parents=True, exist_ok=True)
    temp_path = install_dir / f".{manifest.model_asset_name}.tmp"
    final_path = install_dir / manifest.model_asset_name
    digest = sha256()

    try:
        with urlopen(manifest.source_url) as response, temp_path.open("wb") as handle:
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
            temp_path.unlink(missing_ok=True)
            raise ValueError(
                "SHA-256 mismatch: "
                f"expected {manifest.sha256} but downloaded {observed_sha256}"
            )

        temp_path.replace(final_path)
        return InstalledModel(path=final_path, sha256=observed_sha256)
    finally:
        if temp_path.exists():
            temp_path.unlink()


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
