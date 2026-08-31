import hashlib
import importlib.util
import io
from pathlib import Path

import pytest


def _load_module():
    module_path = Path("tools/fetch_mediapipe_model.py")
    spec = importlib.util.spec_from_file_location("fetch_mediapipe_model", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._buffer = io.BytesIO(payload)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


def _write_manifest(path: Path, *, sha256: str) -> None:
    path.write_text(
        "\n".join(
            [
                'schema_version: "mediapipe-model-manifest/v1"',
                'model_id: "mediapipe-face-landmarker-v1"',
                'model_asset_name: "face_landmarker.task"',
                'source_url: "https://example.invalid/face_landmarker.task"',
                'published_model_identity: "FaceLandmarker float16 latest"',
                f'sha256: "{sha256}"',
                'retrieved_at: "2026-08-31"',
                'permitted_use_reference: "https://ai.google.dev/edge/mediapipe/solutions/guide#terms"',
            ]
        )
        + "\n"
    )


def test_fetch_rejects_payload_with_mismatched_sha256(tmp_path: Path) -> None:
    module = _load_module()
    manifest_path = tmp_path / "manifest.yaml"
    _write_manifest(manifest_path, sha256=hashlib.sha256(b"good-model").hexdigest())

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        module.install_model(
            manifest_path=manifest_path,
            install_dir=tmp_path / "models",
            urlopen=lambda _url: FakeResponse(b"bad-model"),
        )

    assert not any((tmp_path / "models").glob("*"))


def test_fetch_installs_matching_payload_atomically(tmp_path: Path) -> None:
    module = _load_module()
    payload = b"good-model"
    sha256 = hashlib.sha256(payload).hexdigest()
    manifest_path = tmp_path / "manifest.yaml"
    install_dir = tmp_path / "models"
    _write_manifest(manifest_path, sha256=sha256)

    installed = module.install_model(
        manifest_path=manifest_path,
        install_dir=install_dir,
        urlopen=lambda _url: FakeResponse(payload),
    )

    installed_path = install_dir / "face_landmarker.task"
    assert installed.path == installed_path
    assert installed.sha256 == sha256
    assert installed_path.read_bytes() == payload
    assert list(install_dir.glob("*.tmp")) == []
