import os
import subprocess
import sys
import zipfile
from pathlib import Path


def _run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_built_wheel_runs_entry_points_without_repository_files(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[2]
    dist = tmp_path / "dist"
    environment = tmp_path / "venv"
    outside_repository = tmp_path / "outside"
    outside_repository.mkdir()
    _run("uv", "build", "--wheel", "--out-dir", str(dist), cwd=repository)
    wheel = next(dist.glob("alice-*.whl"))
    _run(
        "uv",
        "venv",
        "--python",
        sys.executable,
        str(environment),
        cwd=outside_repository,
    )
    _run(
        "uv",
        "pip",
        "install",
        "--python",
        str(environment / "bin" / "python"),
        str(wheel),
        cwd=outside_repository,
    )
    clean_env = dict(os.environ)
    clean_env.pop("PYTHONPATH", None)
    camera_help = _run(
        str(environment / "bin" / "alice-camera"),
        "--help",
        cwd=outside_repository,
        env=clean_env,
    )
    passive_help = _run(
        str(environment / "bin" / "alice-passive-capture"),
        "--help",
        cwd=outside_repository,
        env=clean_env,
    )
    hardware_help = _run(
        str(environment / "bin" / "alice-hardware-preflight"),
        "--help",
        cwd=outside_repository,
        env=clean_env,
    )
    hardware_run_help = _run(
        str(environment / "bin" / "alice-hardware-run"),
        "--help",
        cwd=outside_repository,
        env=clean_env,
    )
    probe = _run(
        str(environment / "bin" / "python"),
        "-c",
        (
            "from importlib.metadata import distributions; "
            "from importlib.resources import files; "
            "names=sorted(d.metadata['Name'].lower() for d in distributions() "
            "if d.metadata['Name'].lower().startswith('opencv-')); "
            "print(names); "
            "print(files('alice.resources').joinpath("
            "'passive-blendshape-conclusion.md').read_text()[:31]); "
            "print(files('alice.resources').joinpath("
            "'actuator-identification-conclusion.md').read_text()[:36])"
        ),
        cwd=outside_repository,
        env=clean_env,
    )

    assert "alice-camera" in camera_help
    assert "alice-passive-capture" in passive_help
    assert "Phase 2 hardware" in hardware_help
    assert "Guarded Phase 2" in hardware_run_help
    assert "['opencv-contrib-python']" in probe
    assert "# Passive Blendshape Conclusion" in probe
    assert "# Actuator-identification conclusion" in probe


def test_built_wheel_keeps_ml_dependencies_behind_optional_extra(
    tmp_path: Path,
) -> None:
    """Base installs must not download Torch when no model code is requested."""

    repository = Path(__file__).resolve().parents[2]
    dist = tmp_path / "dist"
    _run("uv", "build", "--wheel", "--out-dir", str(dist), cwd=repository)
    wheel = next(dist.glob("alice-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = archive.read(metadata_name).decode("utf-8")
    requirements = tuple(
        line.removeprefix("Requires-Dist: ")
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist: ")
    )

    for dependency in ("torch", "safetensors"):
        matching = tuple(
            requirement
            for requirement in requirements
            if requirement.split(";", 1)[0].strip().startswith(dependency)
        )
        assert matching
        assert all("extra == 'ml'" in requirement for requirement in matching)
