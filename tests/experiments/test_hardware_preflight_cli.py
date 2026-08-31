from __future__ import annotations

from pathlib import Path

from alice.experiments.hardware_cli import main

ROOT = Path(__file__).parents[2]


def test_cli_defaults_to_dry_run_and_cannot_execute_set_target(capsys: object) -> None:
    result = main(
        [
            "--config",
            str(ROOT / "config/experiments/actuator-identification-hardware.yaml"),
            "--manifest",
            str(ROOT / "hardware/alice-face-v1.yaml"),
        ]
    )

    assert result == 0


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
