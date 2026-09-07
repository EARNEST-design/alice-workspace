import hashlib
import importlib.util
import io
import os
import stat
from concurrent.futures import ThreadPoolExecutor
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


def _pin_temp_manifest(module: object, manifest_path: Path) -> None:
    module.TRACKED_MANIFEST_PATH = manifest_path.resolve()
    model_manifest = module.load_manifest.__globals__["ModelManifest"]
    module.PINNED_MANIFEST = model_manifest.model_validate(
        {
            "schema_version": "mediapipe-model-manifest/v1",
            "model_id": "mediapipe-face-landmarker-v1",
            "model_asset_name": "face_landmarker.task",
            "source_url": "https://example.invalid/face_landmarker.task",
            "published_model_identity": "FaceLandmarker float16 latest",
            "sha256": hashlib.sha256(b"good-model").hexdigest(),
            "retrieved_at": "2026-08-31",
            "permitted_use_reference": (
                "https://ai.google.dev/edge/mediapipe/solutions/guide#terms"
            ),
        }
    )


def test_fetch_rejects_payload_with_mismatched_sha256(tmp_path: Path) -> None:
    module = _load_module()
    manifest_path = tmp_path / "manifest.yaml"
    _write_manifest(manifest_path, sha256=hashlib.sha256(b"good-model").hexdigest())
    _pin_temp_manifest(module, manifest_path)

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        module.install_model(
            manifest_path=manifest_path,
            install_dir=tmp_path / "models",
            urlopen=lambda _url: FakeResponse(b"bad-model"),
        )

    assert not any((tmp_path / "models").glob("*"))


def test_fetch_rejects_non_tracked_manifest_before_download(tmp_path: Path) -> None:
    module = _load_module()
    payload = b"good-model"
    sha256 = hashlib.sha256(payload).hexdigest()
    manifest_path = tmp_path / "manifest.yaml"
    _write_manifest(manifest_path, sha256=sha256)

    with pytest.raises(ValueError, match="tracked manifest"):
        module.install_model(
            manifest_path=manifest_path,
            install_dir=tmp_path / "models",
            urlopen=lambda _url: (_ for _ in ()).throw(AssertionError("downloaded")),
        )


def test_fetch_rejects_tracked_manifest_with_altered_pinned_values(
    tmp_path: Path,
) -> None:
    # Catch a caller-controlled change to tracked provenance without reaching download.
    module = _load_module()
    payload = b"good-model"
    sha256 = hashlib.sha256(payload).hexdigest()
    manifest_path = tmp_path / "manifest.yaml"
    _write_manifest(manifest_path, sha256=sha256)
    altered_text = manifest_path.read_text().replace(
        'source_url: "https://example.invalid/face_landmarker.task"',
        'source_url: "https://example.invalid/other.task"',
    )
    manifest_path.write_text(altered_text)
    module.TRACKED_MANIFEST_PATH = manifest_path.resolve()

    with pytest.raises(ValueError, match="pinned provenance"):
        module.install_model(
            manifest_path=manifest_path,
            install_dir=tmp_path / "models",
            urlopen=lambda _url: (_ for _ in ()).throw(AssertionError("downloaded")),
        )


def test_fetch_installs_matching_payload_atomically(tmp_path: Path) -> None:
    module = _load_module()
    payload = b"good-model"
    sha256 = hashlib.sha256(payload).hexdigest()
    manifest_path = tmp_path / "manifest.yaml"
    install_dir = tmp_path / "models"
    _write_manifest(manifest_path, sha256=sha256)
    _pin_temp_manifest(module, manifest_path)

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
    assert stat.S_IMODE(installed_path.stat().st_mode) == 0o600


def test_fetch_ignores_preexisting_fixed_temp_symlink(tmp_path: Path) -> None:
    module = _load_module()
    payload = b"good-model"
    manifest_path = tmp_path / "manifest.yaml"
    install_dir = tmp_path / "models"
    install_dir.mkdir()
    victim = tmp_path / "victim"
    victim.write_bytes(b"do-not-touch")
    legacy_temp = install_dir / ".face_landmarker.task.tmp"
    legacy_temp.symlink_to(victim)
    _write_manifest(manifest_path, sha256=hashlib.sha256(payload).hexdigest())
    _pin_temp_manifest(module, manifest_path)

    module.install_model(
        manifest_path=manifest_path,
        install_dir=install_dir,
        urlopen=lambda _url: FakeResponse(payload),
    )

    assert victim.read_bytes() == b"do-not-touch"
    assert legacy_temp.is_symlink()


def test_fetch_replaces_destination_symlink_without_following_it(
    tmp_path: Path,
) -> None:
    module = _load_module()
    payload = b"good-model"
    manifest_path = tmp_path / "manifest.yaml"
    install_dir = tmp_path / "models"
    install_dir.mkdir()
    victim = tmp_path / "victim"
    victim.write_bytes(b"do-not-touch")
    destination = install_dir / "face_landmarker.task"
    destination.symlink_to(victim)
    _write_manifest(manifest_path, sha256=hashlib.sha256(payload).hexdigest())
    _pin_temp_manifest(module, manifest_path)

    module.install_model(
        manifest_path=manifest_path,
        install_dir=install_dir,
        urlopen=lambda _url: FakeResponse(payload),
    )

    assert victim.read_bytes() == b"do-not-touch"
    assert destination.is_file() and not destination.is_symlink()
    assert destination.read_bytes() == payload


def test_concurrent_fetches_use_distinct_temps_and_publish_valid_model(
    tmp_path: Path,
) -> None:
    module = _load_module()
    payload = b"good-model"
    manifest_path = tmp_path / "manifest.yaml"
    install_dir = tmp_path / "models"
    _write_manifest(manifest_path, sha256=hashlib.sha256(payload).hexdigest())
    _pin_temp_manifest(module, manifest_path)

    def install() -> object:
        return module.install_model(
            manifest_path=manifest_path,
            install_dir=install_dir,
            urlopen=lambda _url: FakeResponse(payload),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: install(), range(2)))

    assert len(results) == 2
    assert (install_dir / "face_landmarker.task").read_bytes() == payload
    assert sorted(path.name for path in install_dir.iterdir()) == [
        "face_landmarker.task"
    ]


def test_fetch_cleans_unique_temp_when_download_raises(tmp_path: Path) -> None:
    module = _load_module()
    manifest_path = tmp_path / "manifest.yaml"
    install_dir = tmp_path / "models"
    _write_manifest(
        manifest_path,
        sha256=hashlib.sha256(b"good-model").hexdigest(),
    )
    _pin_temp_manifest(module, manifest_path)

    class BrokenResponse(FakeResponse):
        def read(self, size: int = -1) -> bytes:
            raise OSError("download interrupted")

    with pytest.raises(OSError, match="download interrupted"):
        module.install_model(
            manifest_path=manifest_path,
            install_dir=install_dir,
            urlopen=lambda _url: BrokenResponse(b""),
        )

    assert list(install_dir.iterdir()) == []


def test_fetch_fsyncs_model_and_install_directory(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    payload = b"good-model"
    manifest_path = tmp_path / "manifest.yaml"
    install_dir = tmp_path / "models"
    _write_manifest(manifest_path, sha256=hashlib.sha256(payload).hexdigest())
    _pin_temp_manifest(module, manifest_path)
    real_fsync = os.fsync
    fsynced_modes: list[int] = []

    def recording_fsync(fd: int) -> None:
        fsynced_modes.append(os.fstat(fd).st_mode)
        real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", recording_fsync)

    module.install_model(
        manifest_path=manifest_path,
        install_dir=install_dir,
        urlopen=lambda _url: FakeResponse(payload),
    )

    assert any(stat.S_ISREG(mode) for mode in fsynced_modes)
    assert any(stat.S_ISDIR(mode) for mode in fsynced_modes)
