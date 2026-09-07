from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

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


def _enabled_config() -> SimpleNamespace:
    return SimpleNamespace(
        run_id="run",
        approval_id="approval",
        enable_token=SimpleNamespace(get_secret_value=lambda: "token"),
        electrical_evidence_path="evidence.yaml",
        detector_model_path="model.task",
    )


def test_power_enable_rejection_explicitly_cancels_prepared_handle(
    monkeypatch, tmp_path: Path
) -> None:
    prepared = SimpleNamespace(challenge=object())
    bound = SimpleNamespace(challenge=object())
    cancelled: list[object] = []
    events: list[str] = []
    monkeypatch.setattr(cli, "_load_config", lambda _: _enabled_config())
    monkeypatch.setattr(cli, "_load", lambda *_: object())
    monkeypatch.setattr(cli, "_prepare", lambda **_: prepared)
    monkeypatch.setattr(
        cli,
        "_bind_output",
        lambda **_: events.append("reserve-output") or bound,
    )
    monkeypatch.setattr(cli, "_cancel", cancelled.append)
    monkeypatch.setattr(
        cli, "_input", lambda _: events.append("power-on-prompt") or "no"
    )

    result = cli.main(
        [
            "--config",
            "config",
            "--manifest",
            "manifest",
            "--approval",
            "approval",
            "--attestation",
            "attestation",
            "--output",
            str(tmp_path / "output"),
            "--enable-hardware",
        ]
    )

    assert result == 2
    assert events == ["reserve-output", "power-on-prompt"]
    assert cancelled == [bound]


def test_power_removal_eof_explicitly_abandons_pending_handle(
    monkeypatch, tmp_path: Path
) -> None:
    challenge = SimpleNamespace(
        run_id="run",
        challenge_id="challenge",
        config_sha256="a" * 64,
        manifest_sha256="b" * 64,
        electrical_evidence_sha256="c" * 64,
        output_identity_sha256="e" * 64,
        challenge_sha256="d" * 64,
    )
    prepared = SimpleNamespace(challenge=challenge)
    pending = SimpleNamespace(
        draft=SimpleNamespace(
            run_id="run",
            challenge_id="challenge",
            config_sha256="a" * 64,
            manifest_sha256="b" * 64,
            draft_sha256="e" * 64,
        )
    )
    abandoned: list[object] = []
    replies = iter([cli._POWER_ON])

    def input_then_eof(_: str) -> str:
        try:
            return next(replies)
        except StopIteration as error:
            raise EOFError from error

    monkeypatch.setattr(cli, "_load_config", lambda _: _enabled_config())
    monkeypatch.setattr(cli, "_load", lambda *_: object())
    monkeypatch.setattr(cli, "_prepare", lambda **_: prepared)
    monkeypatch.setattr(cli, "_bind_output", lambda **_: prepared)
    monkeypatch.setattr(cli, "_execute", lambda **_: pending)
    monkeypatch.setattr(cli, "_abandon", abandoned.append)
    monkeypatch.setattr(cli, "_input", input_then_eof)

    result = cli.main(
        [
            "--config",
            "config",
            "--manifest",
            "manifest",
            "--approval",
            "approval",
            "--attestation",
            "attestation",
            "--output",
            str(tmp_path / "output"),
            "--enable-hardware",
        ]
    )

    assert result == 3
    assert abandoned == [pending]


def test_execution_failure_still_requires_and_records_power_off(
    monkeypatch, tmp_path: Path
) -> None:
    challenge = SimpleNamespace(
        run_id="run",
        challenge_id="challenge",
        config_sha256="a" * 64,
        manifest_sha256="b" * 64,
        electrical_evidence_sha256="c" * 64,
        output_identity_sha256="e" * 64,
        challenge_sha256="d" * 64,
    )
    prepared = SimpleNamespace(challenge=challenge)
    events: list[str] = []
    replies = iter([cli._POWER_ON, cli._POWER_OFF])
    monkeypatch.setattr(cli, "_load_config", lambda _: _enabled_config())
    monkeypatch.setattr(cli, "_load", lambda *_: object())
    monkeypatch.setattr(cli, "_prepare", lambda **_: prepared)
    monkeypatch.setattr(
        cli, "_bind_output", lambda **_: events.append("reserve") or prepared
    )
    monkeypatch.setattr(
        cli, "_input", lambda _: events.append("prompt") or next(replies)
    )
    monkeypatch.setattr(
        cli, "_execute", lambda **_: (_ for _ in ()).throw(RuntimeError("primary"))
    )
    monkeypatch.setattr(
        cli, "_record_failed_power_off", lambda **_: events.append("record-off")
    )

    with pytest.raises(RuntimeError, match="primary"):
        cli.main(
            [
                "--config",
                "config",
                "--manifest",
                "manifest",
                "--approval",
                "approval",
                "--attestation",
                "attestation",
                "--output",
                str(tmp_path / "output"),
                "--enable-hardware",
            ]
        )

    assert events == ["reserve", "prompt", "prompt", "record-off"]
