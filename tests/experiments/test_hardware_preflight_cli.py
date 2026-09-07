from __future__ import annotations

from pathlib import Path

import pytest

from alice.experiments.hardware_cli import main

ROOT = Path(__file__).parents[2]


def test_cli_defaults_to_dry_run_and_reports_electrical_blocker(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "--config",
            str(ROOT / "config/experiments/actuator-identification-hardware.yaml"),
            "--manifest",
            str(ROOT / "hardware/alice-face-v1.yaml"),
        ]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "mode=dry-run" in output
    assert "electrical_evidence=unresolved" in output


def test_cli_refuses_enable_without_interactive_confirmation(capsys: object) -> None:
    result = main(
        [
            "--config",
            str(ROOT / "config/experiments/actuator-identification-hardware.yaml"),
            "--manifest",
            str(ROOT / "hardware/alice-face-v1.yaml"),
            "--enable-hardware",
        ],
        input_fn=lambda _: "wrong",
    )

    assert result == 2
