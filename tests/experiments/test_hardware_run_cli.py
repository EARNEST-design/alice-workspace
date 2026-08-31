from __future__ import annotations

from pathlib import Path

import alice.experiments.hardware_run_cli as cli

ROOT = Path(__file__).parents[2]


def test_hardware_run_cli_defaults_to_non_actuating_verification(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "_prepare",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not prepare hardware")),
    )

    result = cli.main(
        [
            "--config",
            str(ROOT / "config/experiments/actuator-identification-hardware.yaml"),
            "--manifest",
            str(ROOT / "hardware/alice-face-v1.yaml"),
        ]
    )

    assert result == 0


def test_repository_placeholder_config_cannot_enter_hardware_prepare(
    monkeypatch,
) -> None:
    called = False

    def forbidden(**_: object) -> object:
        nonlocal called
        called = True
        raise AssertionError

    monkeypatch.setattr(cli, "_prepare", forbidden)
    result = cli.main(
        [
            "--config",
            str(ROOT / "config/experiments/actuator-identification-hardware.yaml"),
            "--manifest",
            str(ROOT / "hardware/alice-face-v1.yaml"),
            "--enable-hardware",
            "--approval",
            "missing",
            "--attestation",
            "missing",
            "--output",
            "unused",
        ]
    )
    assert result == 2
    assert called is False
