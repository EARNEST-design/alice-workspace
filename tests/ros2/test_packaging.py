from __future__ import annotations

import subprocess
import sys
import zipfile
from email.parser import Parser
from pathlib import Path
from shutil import copy2, which

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet


@pytest.fixture(scope="module")
def wheel_metadata(tmp_path_factory: pytest.TempPathFactory):
    repository = Path(__file__).resolve().parents[2]
    dist = tmp_path_factory.mktemp("ros2-wheel")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=repository,
        check=True,
    )
    wheel = next(dist.glob("alice-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        metadata_path = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        return Parser().parsestr(archive.read(metadata_path).decode())


def _requirements_for_extra(metadata, extra: str) -> set[str]:
    selected: set[str] = set()
    for raw_requirement in metadata.get_all("Requires-Dist", []):
        requirement = Requirement(raw_requirement)
        if requirement.marker is not None and requirement.marker.evaluate(
            {"extra": extra}
        ):
            selected.add(requirement.name.lower())
    return selected


def test_wheel_supports_python314_and_keeps_camera_dependencies_optional(
    wheel_metadata,
) -> None:
    supported_python = SpecifierSet(wheel_metadata["Requires-Python"])
    assert "3.12" in supported_python
    assert "3.14" in supported_python
    assert "3.15" not in supported_python

    base = {
        Requirement(raw).name.lower()
        for raw in wheel_metadata.get_all("Requires-Dist", [])
        if Requirement(raw).marker is None
    }
    assert "mediapipe" not in base
    assert "opencv-contrib-python" not in base
    assert {"mediapipe", "opencv-contrib-python"} <= _requirements_for_extra(
        wheel_metadata, "perception"
    )


def test_full_extra_preserves_legacy_complete_install(wheel_metadata) -> None:
    assert {
        "mediapipe",
        "opencv-contrib-python",
        "pocket-tts",
        "sounddevice",
        "safetensors",
        "torch",
    } <= _requirements_for_extra(wheel_metadata, "full")


def test_core_face_runtime_import_does_not_load_optional_ml() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from alice.speech.face_runtime import FaceRuntime; "
                "import sys; "
                "assert 'torch' not in sys.modules; "
                "assert 'mediapipe' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_docker_context_keeps_reviewed_runtime_assets_and_excludes_local_files(
    tmp_path: Path,
) -> None:
    if which("docker") is None:
        pytest.skip("Docker is required to inspect the effective build context")
    daemon = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is required to inspect the effective build context")

    repository = Path(__file__).resolve().parents[2]
    context = tmp_path / "context"
    output = tmp_path / "rootfs"
    context.mkdir()
    copy2(repository / ".dockerignore", context / ".dockerignore")
    (context / "Dockerfile.context-test").write_text(
        "FROM scratch\nCOPY . /context\n"
    )

    required = (
        "pyproject.toml",
        "uv.lock",
        "src/alice/runtime.py",
        "tests/ros2/test_runtime.py",
        "ros2_ws/src/alice_nodes/package.xml",
        "infra/ros2/entrypoint.sh",
        "config/speech/sync-v1.json",
        "hardware/alice-face-v1.yaml",
    )
    excluded = (
        "hardware/electrical/unreviewed-local-note.md",
        "infra/ros2/.env.local",
        "src/alice/artifacts/local.json",
        "config/models/local-face-landmarker.task",
        "src/alice/__pycache__/runtime.pyc",
    )
    for relative in (*required, *excluded):
        path = context / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)

    result = subprocess.run(
        [
            "docker",
            "build",
            "--network",
            "none",
            "--file",
            str(context / "Dockerfile.context-test"),
            "--output",
            f"type=local,dest={output}",
            str(context),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    for relative in required:
        assert (output / "context" / relative).is_file(), relative
    for relative in excluded:
        assert not (output / "context" / relative).exists(), relative
