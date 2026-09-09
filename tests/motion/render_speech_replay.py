"""Reproduce the real-voice QA run; no device or actuation entry point."""

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

import numpy as np
from speech_replay_helpers import replay_speech

from alice.contracts.speech import SpeechPlan
from alice.speech.artifacts import write_artifacts
from alice.speech.synthesis import PocketSynthesizer
from alice.speech.timeline import prepare_speech

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New artifact directory")
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=False)
    source = ROOT / "config/speech/alice-sync-test.json"
    plan = SpeechPlan.model_validate_json(source.read_text())
    engine = PocketSynthesizer(offline=True)
    started = time.perf_counter()
    speech = prepare_speech(plan, engine)
    elapsed = time.perf_counter() - started
    report = {
        "schema_version": "speech-streaming-qualification/v1",
        "voice": plan.voice,
        "source_plan_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "audio_duration_s": len(speech.audio.pcm) / speech.audio.sample_rate,
        "synthesis_seconds": elapsed,
        "modes": {},
        "physical_test_executed": False,
    }
    for mode in ("speech-filtered", "neutral-priors-only"):
        horizon, metrics = replay_speech(speech, mode=mode)
        directory = output / mode
        write_artifacts(
            speech, directory, synthesis_seconds=elapsed, expression=horizon
        )
        timeline = json.loads((directory / "timeline.json").read_text())
        positions = np.array(
            [
                next(
                    t["normalized_position"]
                    for t in u["targets"]
                    if t["actuator_name"] == "mouth_open"
                )
                for u in timeline["motion"]
            ]
        )
        seconds = np.array([u["offset_s"] for u in timeline["motion"]])
        hop = 1 / speech.config.cadence_hz
        intervals = np.diff(np.r_[-hop, seconds])
        velocity = np.diff(np.r_[0, positions]) / intervals
        acceleration = np.diff(np.r_[0, velocity]) / intervals
        metrics["requested_jaw_trace"] = {
            "interpretation": (
                "Command differences only; not observed motion or fitted limits."
            ),
            "sample_interval_s": hop,
            "range": [float(positions.min()), float(positions.max())],
            "first_step_from_home": float(positions[0]),
            "last_target": float(positions[-1]),
            "max_speed_per_s": float(np.abs(velocity).max()),
            "max_acceleration_per_s2": float(np.abs(acceleration).max()),
            "intervals_above_unfitted_speed_prior_2_per_s": int(
                (np.abs(velocity) > 2).sum()
            ),
            "intervals_above_unfitted_acceleration_prior_4_per_s2": int(
                (np.abs(acceleration) > 4).sum()
            ),
            "initial_assumption": (
                "Home at rest one 20 ms proposal interval before sample 0"
            ),
        }
        metrics["artifact_manifest_sha256"] = hashlib.sha256(
            (directory / "manifest.json").read_bytes()
        ).hexdigest()
        report["modes"][mode] = metrics
    (output / "metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "index.html").write_text("""<!doctype html><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Alice · Azelma synchronization test</title>
    <style>body{font:18px/1.6 system-ui;background:#151b20;color:#e8e6e1;
    max-width:850px;margin:50px auto;padding:20px}a{color:#7ed9c3}
    section{background:#1e282f;border-radius:16px;padding:24px;margin:25px 0}
    h1{font-size:36px}</style>
    <h1>Alice · Azelma synchronization test</h1>
    <p>Azelma is Alice’s selected voice. These two replays use the same locally
    generated recording and audio clock. Press Play inside either preview.</p>
    <section><h2>Current expression pipeline</h2>
    <p>The speech cues reach the intent filter. Its support set is empty, so it
    retains the neutral expression while the mouth follows speech.</p>
    <a href="speech-filtered/preview.html">Play speech with current ML pipeline</a>
    </section><section><h2>Procedural motion demo</h2>
    <p>Speech is overlaid on the completed engine’s seeded neutral blink/gaze and
    head-motion fixture. This demo uses procedural timing, with zero learned
    residual weights; the changing speech emotion cues do not drive these
    movements.</p><a href="neutral-priors-only/preview.html">
    Play speech with procedural motion</a></section>
    <p>Both previews show requested mouth aperture and all composed channel values.
    No actuator commands were sent. The speech trace is not approved for direct
    hardware execution.</p>
    <p><a href="metrics.json">Replay measurements</a></p>""")
    inputs = [
        Path(__file__),
        ROOT / "tests/motion/speech_replay_helpers.py",
        ROOT / "tests/motion/test_composed_streaming.py",
        ROOT / "config/affect/affect-vector-v1.yaml",
        ROOT / "config/affect/intent-filter-v1.yaml",
        source,
        *sorted((ROOT / "config/models").glob("*.yaml")),
    ]
    for path in inputs:
        retained = output / "inputs" / path.relative_to(ROOT)
        retained.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, retained)
    manifest = {
        "actuation_mode": "none",
        "source": (
            "Assistant-authored operator-requested text; pretrained stock Azelma "
            "preset; no participant recording."
        ),
        "voice_origin": "VCTK p303; Kyutai tts-voices preset embedding",
        "voice_license": "CC BY 4.0",
        "voice_source": "https://huggingface.co/kyutai/tts-voices/blob/main/README.md",
        "conclusion": (
            "Software integration verified. Current empty support stays fallback. "
            "Raw speech proposals exceed current response priors; "
            "physical trial not run."
        ),
        "files": {
            str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob("*"))
            if p.is_file()
        },
        "replay_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
