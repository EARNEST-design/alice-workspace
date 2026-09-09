import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_timeline import SyntheticVoice, plan

from alice.speech.artifacts import write_artifacts
from alice.speech.timeline import prepare_speech


def test_default_trial_verifies_without_audio_or_serial(tmp_path, monkeypatch):
    from alice.experiments.jaw_trial_cli import main
    from alice.hardware.maestro_adapter import MaestroAdapter

    def forbidden(*args, **kwargs):
        raise AssertionError("default invocation must not open devices")

    monkeypatch.setattr(MaestroAdapter, "open", forbidden)
    source = tmp_path / "source"
    write_artifacts(
        prepare_speech(plan(), SyntheticVoice()), source, synthesis_seconds=1
    )
    output = tmp_path / "trial"
    assert main(["--recording", str(source), "--output", str(output)]) == 0
    assert (output / "manifest.json").is_file()
    assert not (output / "commands.json").exists()


def test_scoped_manifest_preserves_jaw_calibration_and_unknown_electrical_margin():
    from alice.experiments.jaw_trial_cli import scoped_manifest
    from alice.hardware.manifest import load_manifest

    full = load_manifest(Path(__file__).parents[2] / "hardware/alice-face-v1.yaml")
    scope = scoped_manifest(full)
    assert scope.actuators == (full.actuator("mouth_open"),)
    assert scope.calibration_sha256 != full.calibration_sha256
    ids = {r.requirement_id for r in scope.preflight_requirements}
    assert "electrical-current-limit-verified" not in ids
    assert ids == {
        "maestro-command-interface-role-verified",
        "attended-session-authorized",
    }


@pytest.mark.parametrize(
    "home,completed,fast",
    [
        (0, False, False),
        (0, True, False),
        (4500, False, False),
        (4612, True, False),
        (5059, False, False),
        (5059, True, False),
        (5059, True, True),
        (5059, False, True),
    ],
)
def test_explicit_physical_run_has_no_repeated_readiness_or_power_off_prompt(
    tmp_path, monkeypatch, home, completed, fast
):
    from alice.experiments import jaw_trial_cli as cli
    from alice.hardware import maestro_adapter

    source, output = tmp_path / "source", tmp_path / "trial"
    write_artifacts(
        prepare_speech(plan(), SyntheticVoice()), source, synthesis_seconds=1
    )
    events = []
    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(
            check_output_settings=lambda **kw: None,
            query_devices=lambda **kw: {"name": "TEST-ONLY"},
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_linux_usb_identity",
        lambda path: SimpleNamespace(serial_number="00037376", interface_number="00"),
    )
    monkeypatch.setattr(cli, "_check_owners", lambda full: None)

    class FakeMaestro:
        def __init__(self, **kwargs):
            self.jaw_response_override = None

        def enable_fast_jaw_response(self, token):
            events.append("fast-response")
            self.jaw_response_override = {"status": "active"}

        def open(self, token):
            events.append("open")

        def read_only_preflight(self, names):
            assert names == ("mouth_open",)
            return SimpleNamespace(
                controller_error_register=0,
                positions_qus={"mouth_open": home},
                observed_monotonic_ns=cli.time.monotonic_ns(),
                model_dump=lambda **kw: {"jaw_qus": home},
            )

        def initialize_disabled_jaw_home(self, token):
            assert home == 0
            events.append("initialize-jaw-home")
            return SimpleNamespace(
                controller_error_register=0,
                positions_qus={"mouth_open": 5059},
                observed_monotonic_ns=cli.time.monotonic_ns(),
                model_dump=lambda **kw: {"jaw_qus": 5059},
            )

        def apply(self, request):
            pytest.fail("root orchestration test must not send commands")

        def close(self):
            events.append("close-attempted")
            if self.jaw_response_override:
                self.jaw_response_override["status"] = "restored"
            if not completed:
                raise RuntimeError("injected close error")

    monkeypatch.setattr(maestro_adapter, "MaestroAdapter", FakeMaestro)

    def forbidden_prompt(prompt):
        pytest.fail("explicit attended trial must not ask again")

    def audio(*args, **kwargs):
        events.append("audio-attempted")
        if not completed:
            raise RuntimeError("injected audio failure")
        kwargs["telemetry"]["controller_home_confirmed"] = True

    monkeypatch.setattr("builtins.input", forbidden_prompt)
    monkeypatch.setattr(cli, "run_jaw_playback", audio)
    assert cli.main(
        ["--recording", str(source), "--output", str(output), "--enable-hardware"]
        + (["--stream-targets", "--fast-jaw-response"] if fast else [])
    ) == (0 if completed else 2)
    report = json.loads((output / "manifest.json").read_text())
    assert "power_removal_confirmed" not in report
    assert report["readiness"]["source"] == "explicit-hardware-invocation"
    assert events[-1] == "close-attempted"
    if fast:
        assert events.index("fast-response") < events.index("audio-attempted")
        assert report["jaw_response_override"]["status"] == "restored"
    assert report["status"] == ("completed" if completed else "aborted")
    if home == 0:
        assert "initialize-jaw-home" in events
        assert report["initialization"]["target_qus"] == 5059
    if home == 4500:
        assert "audio-attempted" not in events
        assert "outside the trial range" in report["error"]
    elif not completed:
        assert "injected audio failure" in report["error"]


@pytest.mark.parametrize(
    "exists,stderr,returncode",
    [(False, "", 1), (True, "Permission denied", 1), (True, "", 0)],
)
def test_ownership_check_rejects_missing_ports_errors_and_existing_owners(
    monkeypatch, exists, stderr, returncode
):
    from alice.experiments import jaw_trial_cli as cli

    full = cli.load_manifest(cli.ROOT / "hardware/alice-face-v1.yaml")

    def resolve(path, *, strict):
        assert strict
        if not exists:
            raise FileNotFoundError(path)
        return path

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(
        cli,
        "_resolve_linux_usb_identity",
        lambda path: SimpleNamespace(
            serial_number="00037376", interface_number=path[-2:]
        ),
    )
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=returncode, stderr=stderr, stdout=""
        ),
    )
    with pytest.raises((ValueError, FileNotFoundError)):
        cli._check_owners(full)


def test_fast_response_requires_explicit_hardware_streaming(tmp_path):
    from alice.experiments.jaw_trial_cli import main

    with pytest.raises(SystemExit) as error:
        main(
            [
                "--recording",
                str(tmp_path),
                "--output",
                str(tmp_path),
                "--fast-jaw-response",
            ]
        )
    assert error.value.code == 2
