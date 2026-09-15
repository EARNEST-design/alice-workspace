from __future__ import annotations

import subprocess
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

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
