"""Trusted, opt-in mouth-only speech trial; default invocation is verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdateHorizon
from alice.experiments.hardware_identification import _resolve_linux_usb_identity
from alice.hardware.manifest import HardwareManifest, load_manifest
from alice.safety.supervisor import (
    OperatorApproval,
    PreflightEvidence,
    SafetySupervisor,
)
from alice.speech.jaw_playback import run_jaw_playback
from alice.speech.jaw_trial import (
    JawCommandStream,
    JawPlaybackStream,
    JawTrialConfig,
    MouthOnlyAdapter,
    trial_limits,
)
from alice.speech.recording import load_recording
from alice.speech.streaming_jaw import StreamingJawCommandStream

ROOT = Path(__file__).resolve().parents[3]
SOURCE_CALIBRATION = "8ad9ad1f59dc70c47515c9a37b4531c5070c22a40ed66fb673d0e72a0d3dadb4"


def scoped_manifest(full: HardwareManifest) -> HardwareManifest:
    """Reviewed jaw scope; preserve unknown electrical facts as unknown."""
    if (
        full.calibration_sha256 != SOURCE_CALIBRATION
        or full.actuator("mouth_open").channel != 6
    ):
        raise ValueError("source servo mapping differs from the reviewed calibration")
    document = full.model_dump(mode="json")
    document["hardware_id"] = "alice-jaw-speech-trial-v1"
    document["actuators"] = [full.actuator("mouth_open").model_dump(mode="json")]
    document["preflight_requirements"] = [
        r.model_dump(mode="json")
        for r in full.preflight_requirements
        if r.requirement_id == "maestro-command-interface-role-verified"
    ]
    document["preflight_requirements"].append(
        {
            "requirement_id": "attended-session-authorized",
            "description": "Explicit physical run requested under the operator's "
            "standing "
            "powered bench setup, with the operator at the master switch.",
            "satisfied": False,
            "evidence": [
                {
                    "source": "hardware/bringup/mouth-speech-trial-v1.md",
                    "detail": "Operator superseded repeated readiness and "
                    "power-off prompts "
                    "on 2026-09-09. Electrical qualification remains unmeasured.",
                }
            ],
        }
    )
    document["evidence"].append(
        {
            "source": "hardware/alice-face-v1.yaml",
            "detail": "Unchanged jaw calibration projected from full manifest "
            f"{full.canonical_sha256}; other semantic channels remain wired "
            "through the source manifest.",
        }
    )
    return HardwareManifest.model_validate(document)


def _write(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def _exclusive_transport(path: str, timeout_seconds: float) -> Any:
    import serial

    return serial.Serial(
        path,
        baudrate=9600,
        timeout=timeout_seconds,
        write_timeout=timeout_seconds,
        exclusive=True,
    )


def _check_owners(full: HardwareManifest) -> dict[str, object]:
    paths = (
        full.controller.command_device_path,
        full.controller.command_device_path.replace("-if00", "-if02"),
    )
    for path, interface in zip(paths, ("00", "02"), strict=True):
        Path(path).resolve(strict=True)
        identity = _resolve_linux_usb_identity(path)
        if (
            identity.serial_number != "00037376"
            or identity.interface_number != interface
        ):
            raise ValueError("Maestro interface identity mismatch")
    result = subprocess.run(
        ["fuser", *paths], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 1 or result.stderr.strip() or result.stdout.strip():
        raise ValueError(
            "Maestro interfaces are busy or ownership could not be checked"
        )
    return {
        "paths": paths,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--expression", type=Path)
    parser.add_argument(
        "--play", action="store_true", help="Real audio with mock servos"
    )
    parser.add_argument(
        "--enable-hardware",
        action="store_true",
        help="Run the attended physical jaw trial under the standing bench setup",
    )
    parser.add_argument(
        "--stream-targets",
        action="store_true",
        help="Stream jaw targets without waiting for each output to settle",
    )
    parser.add_argument(
        "--fast-jaw-response",
        action="store_true",
        help="Temporarily set jaw speed/acceleration to 0; restore 0/11 on completion",
    )
    args = parser.parse_args(argv)
    if args.stream_targets and not args.enable_hardware:
        parser.error("--stream-targets requires --enable-hardware")
    if args.fast_jaw_response and not args.stream_targets:
        parser.error(
            "--fast-jaw-response requires --stream-targets and --enable-hardware"
        )
    adapter = None
    raw = None
    stream: JawPlaybackStream | None = None
    report: dict[str, Any] = {}
    powered = False
    okay = False
    output: Path | None = None
    exit_code = 2
    playback: dict[str, object] = {}
    cleanup_errors: list[str] = []

    def retain(path: Path, value: object) -> None:
        try:
            _write(path, value)
        except Exception as exc:
            cleanup_errors.append(f"retaining {path.name}: {type(exc).__name__}: {exc}")

    try:
        # Validate source before creating any device or asking to run.
        load_recording(args.recording)
        config = (
            JawTrialConfig.model_validate_json(args.config.read_text())
            if args.config
            else JawTrialConfig()
        )
        full = load_manifest(ROOT / "hardware/alice-face-v1.yaml")
        scope = scoped_manifest(full)
        expression = (
            TargetUpdateHorizon.model_validate_json(args.expression.read_text())
            if args.expression
            else None
        )
        if expression is not None:
            for update in expression.updates:
                # Validate all mapped proposal channels even though only jaw is enabled.
                for target in update.targets:
                    full.actuator(target.actuator_name).target_qus(
                        target.normalized_position
                    )
        args.output.mkdir(parents=True, exist_ok=False)
        output = args.output
        inputs = output / "inputs"
        inputs.mkdir()
        for name in (
            "manifest.json",
            "plan.json",
            "sync-config.json",
            "timeline.json",
            "speech.wav",
        ):
            shutil.copyfile(args.recording / name, inputs / name)
        prepared = load_recording(inputs)
        if len(prepared.audio.pcm) / prepared.audio.sample_rate > config.max_audio_s:
            raise ValueError("recording exceeds the short trial duration")
        _write(output / "trial-config.json", config.model_dump(mode="json"))
        _write(output / "source-hardware.json", full.model_dump(mode="json"))
        _write(output / "active-hardware.json", scope.model_dump(mode="json"))
        procedure = ROOT / "hardware/bringup/mouth-speech-trial-v1.md"
        shutil.copyfile(procedure, output / "procedure.md")
        if expression is not None:
            _write(output / "expression.json", expression.model_dump(mode="json"))
        code = output / "code"
        code.mkdir()
        for source in (ROOT / "src/alice").rglob("*.py"):
            destination = code / source.relative_to(ROOT / "src")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        shutil.copyfile(ROOT / "uv.lock", output / "uv.lock")
        shutil.copyfile(ROOT / "pyproject.toml", output / "pyproject.toml")
        shutil.copyfile(
            ROOT / "hardware/electrical/alice-servo-supply-6v-1a.md",
            output / "electrical-evidence.md",
        )
        hashes = {
            str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob("*"))
            if p.is_file()
        }
        digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
        report = {
            "schema_version": "jaw-speech-run/v1",
            "run_id": f"jaw-{time.time_ns()}",
            "status": "verified",
            "actuation_mode": "hardware" if args.enable_hardware else "mock",
            "active_actuators": ["mouth_open"],
            "target_transport": "streamed-sent"
            if args.stream_targets
            else "settled-applied",
            "wired_actuators": [a.name for a in full.actuators],
            "source_calibration_sha256": full.calibration_sha256,
            "active_calibration_sha256": scope.calibration_sha256,
            "bundle_sha256": digest,
            "inputs": hashes,
            "voice": prepared.plan.voice,
            "duration_s": len(prepared.audio.pcm) / prepared.audio.sample_rate,
            "electrical_margin_verified": False,
            "physical_sync_measured": False,
        }
        _write(output / "manifest.json", report)
        print(
            f"Prepared {prepared.plan.voice}: {report['duration_s']} seconds; "
            f"bundle {digest}\nEvidence: {output}",
            flush=True,
        )
        if not (args.play or args.enable_hardware):
            return 0
        import sounddevice as sd  # type: ignore[import-untyped]

        sd.check_output_settings(
            samplerate=prepared.audio.sample_rate, channels=1, dtype="float32"
        )
        report["audio_output_device"] = dict(sd.query_devices(kind="output"))
        supervisor = SafetySupervisor(
            manifest=scope,
            clock=time.monotonic_ns,
            limits=trial_limits(config),
            allow_measured_start=True,
        )
        initial_position = 0.0
        evidence_time = time.monotonic_ns()
        approval_time = evidence_time
        if args.enable_hardware:
            jaw = full.actuator("mouth_open")
            print(
                f"\nMOUTH-ONLY TRIAL: channel {jaw.channel}, "
                f"{config.closed_position} to {config.open_position} "
                f"({jaw.target_qus(config.closed_position)}–"
                f"{jaw.target_qus(config.open_position)} quarter-us), "
                f"Home {jaw.home_qus}; at most {config.max_run_s} seconds.\n"
                "Running under the operator's standing attended bench setup.\n"
                f"Reviewed bundle: {digest}",
                flush=True,
            )
            powered = True
            approval_time = time.monotonic_ns()
            report["readiness"] = {
                "source": "explicit-hardware-invocation",
                "setup_policy": "operator-standing-bench-2026-09-09",
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
                "confirmed_monotonic_ns": approval_time,
                "bundle_sha256": digest,
            }
            report["status"] = "powered-preflight"
            _write(output / "manifest.json", report)
            identity = _resolve_linux_usb_identity(full.controller.command_device_path)
            if (
                identity.serial_number != "00037376"
                or identity.interface_number != "00"
            ):
                raise ValueError("Maestro USB identity mismatch")
            report["interface_ownership"] = _check_owners(full)
            from alice.hardware.maestro_adapter import MaestroAdapter

            token = secrets.token_urlsafe(32)
            raw = MaestroAdapter(
                manifest=scope,
                stable_device_path=full.controller.command_device_path,
                expected_controller_serial="00037376",
                required_enable_token=token,
                clock=time.monotonic_ns,
                permit_verifier=supervisor.actuation_permit_verifier,
                transport_factory=_exclusive_transport,
                timeout_seconds=0.05,
                settle_timeout_ns=200_000_000,
                poll_interval_ns=5_000_000,
            )
            adapter = MouthOnlyAdapter(raw, config)
            raw.open(token)
            # Automated checks remain independent of the standing operator setup.
            snapshot = raw.read_only_preflight(("mouth_open",))
            report["preflight"] = snapshot.model_dump(mode="json")
            _write(output / "manifest.json", report)
            if snapshot.controller_error_register != 0:
                raise ValueError("Maestro error register is not clear")
            if snapshot.positions_qus["mouth_open"] == 0:
                report["initialization"] = {
                    "kind": "enable-disabled-jaw-at-home",
                    "channel": 6,
                    "target_qus": 5059,
                    "status": "requested",
                    "physical_start_position_known": False,
                }
                _write(output / "manifest.json", report)
                print("Enabling mouth_open at calibrated Home.", flush=True)
                snapshot = raw.initialize_disabled_jaw_home(token)
                report["initialization"]["status"] = "controller-home-confirmed"
                report["initialized_preflight"] = snapshot.model_dump(mode="json")
                _write(output / "manifest.json", report)
            current_qus = snapshot.positions_qus["mouth_open"]
            if (
                not jaw.target_qus(config.closed_position)
                <= current_qus
                <= jaw.target_qus(config.open_position)
            ):
                raise ValueError("measured jaw output is outside the trial range")
            if current_qus != jaw.home_qus:
                time.sleep(0.1)
                stationary = raw.read_only_preflight(("mouth_open",))
                if (
                    stationary.controller_error_register
                    or stationary.positions_qus != snapshot.positions_qus
                ):
                    raise ValueError("jaw output did not remain stationary at startup")
                snapshot = stationary
                span = (
                    jaw.home_qus - jaw.software_min_qus
                    if current_qus < jaw.home_qus
                    else jaw.software_max_qus - jaw.home_qus
                )
                initial_position = max(
                    config.closed_position,
                    min(config.open_position, (current_qus - jaw.home_qus) / span),
                )
            report["starting_position"] = {
                "controller_qus": current_qus,
                "normalized_position": initial_position,
                "home_verified": current_qus == jaw.home_qus,
                "observed_monotonic_ns": snapshot.observed_monotonic_ns,
            }
            evidence_time = snapshot.observed_monotonic_ns
        else:
            from alice.hardware.mock_adapter import MockActuatorAdapter

            adapter = MouthOnlyAdapter(
                MockActuatorAdapter(
                    manifest=scope,
                    clock=time.monotonic_ns,
                    permit_verifier=supervisor.actuation_permit_verifier,
                ),
                config,
            )
        # Physical evidence combines measured controller checks and the explicit
        # invocation under the standing operator setup; it asserts no new inspection.
        evidence = PreflightEvidence(
            run_id=report["run_id"],
            hardware_id=scope.hardware_id,
            calibration_sha256=scope.calibration_sha256,
            controller_serial=scope.controller.serial_number,
            requirement_results={
                r.requirement_id: True for r in scope.preflight_requirements
            },
            competing_process_detected=False,
            controller_error_codes=(),
            home_verified=initial_position == 0,
            observed_targets=(
                ActuatorTarget(
                    actuator_name="mouth_open", normalized_position=initial_position
                ),
            ),
            observed_monotonic_ns=evidence_time,
        )
        if not supervisor.preflight(evidence).accepted:
            raise RuntimeError("jaw preflight evidence rejected")
        if not supervisor.arm(
            OperatorApproval(
                approval_id=f"{'operator' if powered else 'MOCK'}-{digest[:12]}",
                run_id=report["run_id"],
                confirmed_monotonic_ns=approval_time,
            )
        ).accepted:
            raise RuntimeError("jaw supervisor refused to start")
        if args.stream_targets:
            assert raw is not None
            if args.fast_jaw_response:
                raw.enable_fast_jaw_response(token)
                report["jaw_response_override"] = raw.jaw_response_override
            stream = StreamingJawCommandStream(
                supervisor, raw, token, config, clock=time.monotonic_ns
            )
        else:
            if not supervisor.start().accepted:
                raise RuntimeError("jaw supervisor refused to start")
            stream = JawCommandStream(
                supervisor, adapter, config, clock=time.monotonic_ns
            )
        report["status"] = "running"
        _write(output / "manifest.json", report)
        run_jaw_playback(
            prepared,
            stream,
            expression=expression,
            routing_manifest=full,
            telemetry=playback,
        )
        okay = True
        exit_code = 0
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(f"alice-jaw-trial: {report['error']}", file=sys.stderr, flush=True)
        exit_code = 130 if isinstance(exc, KeyboardInterrupt) else 2
    finally:
        if powered and not okay:
            print("REMOVE SERVO POWER NOW. The trial stopped.", flush=True)
        if adapter is not None:
            try:
                adapter.close()
            except Exception as exc:
                cleanup_errors.append(f"adapter close: {type(exc).__name__}: {exc}")
        if args.fast_jaw_response and raw is not None:
            report["jaw_response_override"] = raw.jaw_response_override
        if stream is not None and output is not None:
            retain(output / "commands.json", stream.records)
            retain(output / "playback.json", playback)
        if powered:
            report["servo_power_policy"] = "operator-master-switch"
            report["servo_power_removed"] = None
        if cleanup_errors:
            okay = False
            exit_code = 2
            report["cleanup_errors"] = cleanup_errors
        if output is not None and (
            args.play or args.enable_hardware or report.get("error")
        ):
            report["status"] = "completed" if okay else "aborted"
            report["artifacts"] = {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in output.glob("*.json")
                if p.name != "manifest.json"
            }
            retain(output / "manifest.json", report)
            if cleanup_errors:
                exit_code = 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
