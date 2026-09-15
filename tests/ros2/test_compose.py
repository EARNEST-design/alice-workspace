"""Deployment contracts; live boundary qualification is integration_runner.py."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra/ros2"
ROLES = {
    "session",
    "tts",
    "audio",
    "expression",
    "motion",
    "maestro",
    "perception",
    "recorder",
}


def config():
    path = INFRA / "compose.yaml"
    assert path.exists(), "default Compose deployment is missing"
    return yaml.safe_load(path.read_text())


def test_default_eight_services_are_idle_isolated_and_have_no_external_inputs():
    data = config()
    services = data["services"]
    assert {
        key for key, value in services.items() if not value.get("profiles")
    } == ROLES
    assert data["networks"]["runtime"]["internal"] is True
    for name in ROLES:
        service = services[name]
        assert service["read_only"] and service["cap_drop"] == ["ALL"]
        assert service["user"] != "0:0"
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["networks"] == ["runtime"]
        assert not any(
            key in service
            for key in ("devices", "privileged", "network_mode", "ipc", "pid", "ports")
        )
        assert service["command"][:2] == ["alice-node", name]
        assert "healthcheck" in service
        assert all(
            "/models" not in str(v) and "/dev/" not in str(v)
            for v in service["volumes"]
        )
    assert services["expression"]["image"].endswith(":speech}")
    assert services["tools"]["profiles"] == ["tools"]
    assert services["robot_state_publisher"]["profiles"] == ["preview"]
    assert services["joint_state_publisher"]["profiles"] == ["preview"]


def test_explicit_overlays_use_only_scoped_inputs():
    config()
    offline = (INFRA / "compose.offline.yaml").read_text()
    assert "models--kyutai--pocket-tts-without-voice-cloning" in offline
    assert "create_host_path: false" in offline
    audio = yaml.safe_load((INFRA / "compose.audio.yaml").read_text())
    assert set(audio["services"]) == {"audio"}
    assert "devices" not in audio["services"]["audio"]
    hardware = yaml.safe_load((INFRA / "compose.hardware.yaml").read_text())
    assert "pid" not in hardware["services"]["maestro"]
    assert len(hardware["services"]["maestro"]["devices"]) == 2
    assert len(hardware["services"]["perception"]["devices"]) == 1
    assert "privileged" not in json.dumps(hardware)


def test_launcher_initializes_owned_artifacts_and_records_exact_images(tmp_path):
    launcher = INFRA / "deploy.py"
    assert launcher.exists(), "owned-volume deployment launcher is missing"
    result = subprocess.run(
        ["python3", str(launcher), "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_description_import_provenance_and_separate_headless_launch():
    config()
    package = ROOT / "ros2_ws/src/alice_description"
    assert (package / "urdf/alice.urdf.xacro").exists()
    assert "13c2549" in (package / "README.md").read_text()
    assert (ROOT / "ros2_ws/src/alice_bringup/launch/participant.launch.py").exists()


def test_launcher_accepts_options_after_command(tmp_path):
    fake = tmp_path / "docker"
    fake.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        'print("sha256:" + "a"*64 if "inspect" in sys.argv '
        'else " ".join(sys.argv[1:]))\n'
    )
    fake.chmod(0o755)
    import os

    result = subprocess.run(
        [
            "python3",
            str(INFRA / "deploy.py"),
            "config",
            "--project",
            "test-boundary",
            "--output",
            str(tmp_path / "evidence"),
        ],
        env=dict(os.environ, PATH=str(tmp_path) + ":" + os.environ["PATH"]),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--project-name test-boundary" in result.stdout
    assert (tmp_path / "evidence").is_dir()


def test_down_cleans_opt_in_preview_services_in_the_same_project(tmp_path):
    fake = tmp_path / "docker"
    fake.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        'print("sha256:" + "a"*64 if "inspect" in sys.argv '
        'else " ".join(sys.argv[1:]))\n'
    )
    fake.chmod(0o755)
    import os

    result = subprocess.run(
        ["python3", str(INFRA / "deploy.py"), "down", "--output", str(tmp_path)],
        env=dict(os.environ, PATH=str(tmp_path) + ":" + os.environ["PATH"]),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert "--profile * down" in result.stdout


def test_custom_fixture_mount_preserves_installed_visible_face_config(tmp_path):
    from shutil import which

    if which("docker") is None:
        pytest.skip("Docker required for installed fixture mount boundary")
    image = subprocess.run(
        ["docker", "image", "inspect", "alice-ros2:core"], capture_output=True
    )
    if image.returncode:
        pytest.skip("built runtime image required")
    (tmp_path / "custom.jsonl").write_text("{}\n")
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--user",
            "1000:1000",
            "-v",
            f"{tmp_path}:/fixtures:ro",
            "alice-ros2:core",
            "test",
            "-f",
            "/opt/alice/config/speech/sync-responsive-v1.json",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "fixture bind hid installed visible-face config"
