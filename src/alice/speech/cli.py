"""Local speech preparation and optional speaker playback with mock motion."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from alice.contracts.motion import TargetUpdateHorizon
from alice.contracts.speech import SpeechPlan, SpeechSyncConfig
from alice.speech.artifacts import write_artifacts
from alice.speech.composer import compose_frame, expression_at_sample
from alice.speech.playback import play_speech
from alice.speech.synthesis import PocketSynthesizer
from alice.speech.timeline import SpeechFrame, prepare_speech


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="alice-speak", description="Local speech and synchronized mock motion"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--plan", type=Path, help="speech-plan/v1 JSON")
    source.add_argument(
        "--clauses", type=Path, help="Incremental speech-clause/v1 JSONL"
    )
    parser.add_argument("--output", type=Path, required=True, help="New run directory")
    parser.add_argument("--sync-config", type=Path)
    parser.add_argument(
        "--expression", type=Path, help="Relative-time expression horizon JSON"
    )
    parser.add_argument(
        "--offline", action="store_true", help="Require cached model artifacts"
    )
    parser.add_argument(
        "--play", action="store_true", help="Play speakers; motion remains mock"
    )
    parser.add_argument(
        "--expression-mode",
        choices=("authored", "learned-fallback"),
        default="learned-fallback",
        help="Incremental expression source",
    )
    parser.add_argument("--config-root", type=Path, default=Path("config"))
    args = parser.parse_args(argv)
    try:
        if args.clauses:
            if args.expression or args.sync_config:
                raise ValueError(
                    "incremental mode uses --config-root sync and expression policy"
                )
            from alice.speech.stream_cli import run_stream

            return run_stream(
                args.clauses,
                args.output,
                config_root=args.config_root,
                mode=args.expression_mode,
                play=args.play,
            )
        plan = SpeechPlan.model_validate_json(args.plan.read_text())
        config = (
            SpeechSyncConfig.model_validate_json(args.sync_config.read_text())
            if args.sync_config
            else SpeechSyncConfig()
        )
        expression = (
            TargetUpdateHorizon.model_validate_json(args.expression.read_text())
            if args.expression
            else None
        )
        if args.output.exists():
            raise ValueError("output directory already exists; choose a new run path")
        engine = PocketSynthesizer(offline=args.offline)
        started = time.perf_counter()
        prepared = prepare_speech(plan, engine, config)
        manifest = write_artifacts(
            prepared,
            args.output,
            synthesis_seconds=time.perf_counter() - started,
            expression=expression,
        )
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "audio_duration_s": manifest["audio_duration_s"],
                    "synthesis_seconds": manifest["synthesis_seconds"],
                    "actuation_mode": "none",
                    "expression_mode": manifest["expression_mode"],
                }
            )
        )
        if args.play:
            with (args.output / "mock-playback.jsonl").open("x") as log:

                def emit(frame: SpeechFrame) -> None:
                    base = expression_at_sample(
                        expression,
                        sample_index=frame.sample_index,
                        sample_rate=prepared.audio.sample_rate,
                    )
                    update = compose_frame(base, frame, config)
                    log.write(
                        json.dumps(
                            {
                                "frame": frame.model_dump(),
                                "proposal": update.model_dump(),
                            }
                        )
                        + "\n"
                    )

                result = play_speech(prepared, emit)
                print(json.dumps({"playback": result, "actuation_mode": "none"}))
        return 0
    except KeyboardInterrupt:
        print("Speech playback cancelled; speech ownership released.", file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError) as error:
        print(f"alice-speak: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
