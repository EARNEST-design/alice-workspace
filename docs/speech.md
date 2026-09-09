# Local voice and synchronized expression

Implementation lives in the existing `streaming-affect-motion` worktree.
Pocket TTS **3.1.0**, model **english_2026-01**, runs on CPU. The default voice is
the supplied **azelma** preset, selected by the operator after a local voice
audition. Explicit voice choices in existing plans are preserved. No remote
inference or voice cloning is used.

## Run locally

The container entry point is `infra/speech/compose.yaml`. It exposes no devices
or network, reads the host's provisioned Hugging Face cache, and writes artifacts
to the host. The cache is already provisioned on this box. On a new machine,
perform the native first-use command below before starting the offline container.

```bash
mkdir -p artifacts/speech
docker compose -f infra/speech/compose.yaml build
docker compose -f infra/speech/compose.yaml run --rm speech
```

For a repeat, pass a new output directory. Set `ALICE_HF_CACHE` to use a different
provisioned cache. Dependency installation uses the host network at build time
because Docker's default build DNS fails on this box; runtime has no network.

For native development on this box:

```bash
cd /home/alice/alice-workspace/.worktrees/streaming-affect-motion
uv sync --extra speech
uv run --extra speech alice-speak \
  --plan config/speech/alice-introduction.json \
  --output artifacts/speech/introduction
```

First use downloads the model and preset voice into the Hugging Face cache.
The model cache is outside Git; run outputs belong in ignored `artifacts/`.
Open `artifacts/speech/introduction/preview.html` and press Play. This is a
self-contained preview with embedded WAV, clause text, requested affect and
mouth animation. It uses the browser audio clock and works offline.

To require an offline repeat, choose a new output directory:

```bash
HF_HUB_OFFLINE=1 uv run --extra speech alice-speak --offline \
  --plan config/speech/alice-introduction.json \
  --output artifacts/speech/introduction-offline
```

Optional `--play` sends audio to the host's default speaker through sounddevice
and writes `mock-playback.jsonl`. It needs PortAudio (`libportaudio2` on Ubuntu)
and an available speaker; the HTML preview is usable without that dependency.
All motor outputs remain mock semantic proposals. Ctrl-C stops playback and
releases speech ownership. Output underflow aborts instead of continuing motion
against an invalid audio clock. The output directory must not already exist.

## LLM input

`config/speech/speech-plan.schema.json` is the generated JSON Schema. An LLM
should return only a `speech-plan/v1` object with a unique utterance ID, identified
source, seed, preset voice, and short clause segments. Each segment has text,
an explicit optional pause and ordered affect cues starting at progress 0.
Vectors are `[valence, arousal, dominance]`, each from -1 to 1; intensity and
progress are from 0 to 1. Progress 1 means the end of that clause's synthesized
audio. Interpolation is linear; the last cue is held to the clause end.

Use short clauses to anchor an expressive change to specific words. Normalized
progress within a clause is approximate; this version does not force-align
words/phonemes. Input rejects unsupported schemas, voice URLs, extra fields,
empty text, nonfinite values, duplicate cue times and oversized utterances.
Limits are 32 segments, 1000 characters per segment and 4000 total; synthesis
also enforces a 120-second generated-content limit by default.

The LLM chooses the utterance and intended expression, not motor commands.
No LLM provider is selected or called by this feature. TTS vocal prosody is not
controlled by these affect cues; they condition the separate expression system.

## Integrate with the motion pipeline

```python
from datetime import datetime, timezone
from time import monotonic_ns
from alice.speech.timeline import prepare_speech
from alice.speech.synthesis import PocketSynthesizer

engine = PocketSynthesizer(offline=True)  # reuse in a dedicated speech process
prepared = prepare_speech(plan, engine)
intent = prepared.affect_intent(
    played_sample, now_ns=monotonic_ns(), captured_at=datetime.now(timezone.utc)
)
# Feed intent through existing IntentFilter -> expression generator.
# Compose with compose_frame(expression_update, prepared.at_sample(played_sample),
#                            prepared.config).
# Validate the COMPOSED proposal using controller response + SafetySupervisor.
```

The real-time hook is `play_speech(prepared, emit)`. It calls `emit(SpeechFrame)`
at the current DAC sample position, coalesces missed frames, and emits a terminal
release on completion, cancellation or failure. Consumers can use the frame's
sample index to obtain a short-lived `AffectIntent` and blend their expression.
Keep callbacks fast. Use a dedicated process because Pocket TTS uses global Torch
RNG/thread settings; the adapter restores these but cannot isolate concurrent ML
threads. Imports and model loading never open serial, cameras or audio devices.

For a generated expression sequence, pass `--expression path/to/horizon.json`
using the existing `target-update-horizon/v1` schema. Offsets are seconds from
audio start. Sparse updates accumulate; the expression generator owns interpolation.
The exported timeline contains composed targets and the manifest hashes the input
expression. These are requested targets, not controller outputs or observed motion.

During speech, the waveform envelope owns jaw aperture. Expression influence on
the jaw is small and gated by speech activity; eyes, brows, head and mouth corners
remain with the expression source. Trailing silence releases speech ownership.
The default preview has no supplied expression: it demonstrates requested affect
and jaw timing with an explicitly labeled neutral expression fallback. The ML
support set remains empty until the separate evidence/training work fills it.

## Azelma speech and streaming-engine replay

The operator-selected Azelma demo is `config/speech/alice-sync-test.json`. It has
one short utterance and changing affect cues. To reproduce the software trial:

```bash
HF_HUB_OFFLINE=1 uv run --extra speech python tests/motion/render_speech_replay.py \
  artifacts/speech/my-azelma-replay
```

This repository QA utility reuses the completed streaming-engine test fixture;
it requires the development dependencies. It renders two previews on the same
audio clock: the current empty-support pipeline, which stays neutral, and a
separately labeled neutral procedural blink/gaze demo with zero residual weights.
The latter does not use speech affect to select movements. The utility refuses
to report an empty-support conclusion if the support config becomes nonempty.

The preview animates requested speech aperture and shows composed actuator
proposals underneath. Seeking updates those values from the audio time; channels
not yet introduced by a sparse expression are blank. This is a software timing
test. The raw proposals feed the bounded mouth-only trial executor described below. Measurements and unresolved
hardware integration are recorded in
`docs/experiments/2026-09-09-speech-streaming-replay.md` and
`hardware/speech-timing.md`.

## Artifacts and validation

Each run creates `speech.wav`, `plan.json`, `sync-config.json`, `timeline.json`,
`speech-plan.schema.json`, `preview.html`, and `manifest.json`. The manifest records
the model configuration and identity, package versions, seed, code revision,
working-tree status, timings, mode and artifact checksums. Outputs may contain
operator/LLM text and should be retained according to that text's intended use.

```bash
uv run --extra speech pytest tests/speech -q
uv run --extra speech ruff check src/alice/speech src/alice/contracts/speech.py tests/speech
uv run --extra speech mypy src/alice/speech src/alice/contracts/speech.py
```

Tests use synthetic PCM and fake audio devices. Real CPU/offline measurements are
recorded separately in `docs/experiments/2026-09-09-local-speech.md`.
Physical timing unknowns live in `hardware/speech-timing.md`.

See [ADR 0007](architecture/0007-local-speech-and-affect-synchronization.md) for
the buffering, ownership and interface decisions.

## Attended mouth-only hardware trial

The reviewed procedure is `hardware/bringup/mouth-speech-trial-v1.md`. The source
manifest wires all 11 semantic servo names; this initial trial physically writes
only `mouth_open` on channel 6. Other motion channels remain selectable in the
proposal router for later reviewed trials. The CLI does not expose an option
to enable extra physical channels.

Prepare a retained, device-free trial bundle:

```bash
uv run --extra speech alice-jaw-trial \
  --recording artifacts/speech/azelma-sync-reviewed/neutral-priors-only \
  --expression artifacts/speech/azelma-sync-reviewed/neutral-priors-only/expression.json \
  --config config/speech/jaw-trial-v1.json \
  --output artifacts/speech/jaw-trial-review
```

For a fresh output directory, add `--play` for real audio with mock actuators.
Add `--enable-hardware` to run the operator-requested attended physical trial.
The operator's standing powered setup and master-switch access replace repeated
readiness prompts. The runner checks hardware automatically, initializes disabled
jaw PWM at Home when needed, or starts from a stable measured in-range output.
The clip runs once, then returns to Home. Faults stop authority and audio without
automatic recovery. Successful completion closes serial and leaves power control
to the operator; no OFF acknowledgment is required.

The new short trial explicitly retains the unknown current margin of the
recorded 6 V / 1 A supply. The user-approved full-range profile is limited to the jaw. Artifacts retain source code, dependencies, exact input hashes, config,
procedure, requested/acknowledged commands, audio clock frames and cleanup status.
No camera or microphone is opened.

## Streaming next step

The requested next architecture is incremental LLM clauses plus incremental
Pocket TTS PCM, so playback starts before either the LLM response or TTS audio
is complete. The installed Pocket TTS 3.1.0 exposes `generate_audio_stream`.
ADR 0009 records the queue, playback-clock and emotion-cue design. The current
hardware trial still replays a retained WAV; chunked synthesis is not implemented
yet.

## Camera-verified mouth calibration

Use `config/speech/sync-hardware-v1.json` when preparing Alice speech. It sets
full-open RMS to 0.06 and the mouth range to -1…+1. The original 0.15 RMS plus
narrower range and trajectory smoothing stayed below neutral on the hardware.
The full range is 4608–5440 quarter-microseconds around Home 5059.

```bash
uv run --extra speech alice-speak \
  --plan config/speech/alice-sync-test.json \
  --sync-config config/speech/sync-hardware-v1.json \
  --output artifacts/speech/new-sync
uv run --extra speech alice-jaw-trial \
  --recording artifacts/speech/new-sync \
  --config config/speech/jaw-speech-lead-v1.json \
  --output artifacts/speech/new-sync-hardware --enable-hardware \
  --stream-targets --fast-jaw-response
```

Azelma's camera-observed trial crossed neutral (-1 to +0.576) during speech;
lip gap varied from 7.6 to 32.7 pixels. These are visible travel measurements,
not a precise acoustic-to-mechanical lag estimate. The observer used Alice's
C525 only. Her mouthUpperUpLeft/Right scores were informative; MediaPipe's nominal
jawOpen stayed near zero even during the visibly successful full-range check.

The fast hardware command above streams channel 6 targets while the servo moves,
using independent audio playback and host send timestamps. `--fast-jaw-response`
temporarily sets Maestro jaw speed/acceleration to 0/0 and restores the reviewed
0/11 profile after normal Home confirmation. Zero means unlimited in this controller; software
trajectory limits remain active. Omit that flag to retain the firmware ramp.
The run manifest records the override and restoration outcome. No persistent
controller configuration is changed.

`jaw-speech-lead-v1.json` additionally advances mouth aperture by 100 ms to
compensate software/servo response. Emotion cues stay at the current audio sample.
The default and `jaw-speech-fast-v1.json` use zero lead. With incremental TTS,
this calls for a short PCM lookahead buffer, not full-utterance buffering.
