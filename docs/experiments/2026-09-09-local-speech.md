# Local speech baseline — 2026-09-09

## Configuration and provenance

- Host: alice-brain, Ryzen 5 6600H; CPU inference with two Torch threads.
- Python 3.13.15; Pocket TTS 3.1.0; Torch 2.14.0+cpu.
- Model: `english_2026-01`; voice: upstream `alba` preset; seed: 7.
- Input: `config/speech/alice-introduction.json`, operator-authored synthetic demo.
- Sync: `config/speech/sync-v1.json`; 50 Hz envelope, 0.03 s attack,
  0.06 s release, 0.3 s trailing ownership release.
- No human recordings, participant data, training split, or trained affect
  mapping is involved. Generated text/audio and proposals remain in ignored
  `artifacts/speech/`; configuration and conclusions are retained here.

## Results

All runs generated 8.78 seconds of mono 24 kHz audio, including explicit pauses
and the trailing release. Both native runs used offline mode with cached weights.
"Cold" includes model loading in a fresh Python process; "warm" reuses the model
and voice state in the same process. These are single-run observations, not a
latency distribution or a controlled comparison of deployment environments.

| Run | Preparation seconds | Real-time factor |
| --- | ---: | ---: |
| Native, cold model | 4.791 | 0.546 |
| Native, warm model | 2.277 | 0.259 |
| Container, cold model, network disabled | 9.673 | 1.102 |

All three WAVs have the same SHA-256:
`98f5e6e2f85c79a6ca050d8da03bcf8669bebb811591c823da44c5dd37c40701`.

Run manifests: `artifacts/speech/offline-cold/manifest.json`,
`artifacts/speech/offline-warm/manifest.json`, and
`artifacts/speech/container-offline/manifest.json`. Each includes the model
configuration with pinned upstream weight references, package versions, seed,
timings and checksums for WAV, timeline, input plan, config, schema and preview.
`artifacts/speech/benchmark.json` contains the native comparison.

## Software verification

- Full project suite: **712 passed**, with two existing fork/thread warnings.
- Speech suite: **25 passed** both with the speech extra and in an isolated base
  environment without Pocket TTS installed.
- Wheel tests: **2 passed**, including `alice-speak --help` outside the repository
  without optional speech dependencies and optional-dependency metadata checks.
- Ruff passed; mypy passed for all 68 source files; `git diff --check` passed.
- Independent review findings were reproduced and repaired: cancellation must
  release at the current sample, export must work without Git, and fake-model
  tests must not require the optional distribution. The reviewer verified fixes.
- Browser preview loaded and played; its clock and affect values advanced from
  audio playback and the mouth returned closed at the end.
- Docker image built from a digest-pinned Python base. Offline synthesis passed
  with no network, read-only filesystem/cache, no devices and dropped capabilities.
  Build-only host networking was necessary because default Docker DNS could not
  resolve package hosts.

## Conclusion and limitations

Local CPU speech preparation and software mouth/affect timing work. Warm native
synthesis was faster than the resulting audio duration in this small sample.
The same seed produced identical WAVs across cold/warm native and container runs.
The entire utterance is buffered before playback, so preparation adds startup
latency. This result does not establish streaming latency, vocal emotion control,
phoneme alignment, naturalness, or physically realized robot synchronization.

Speaker playback was checked through the browser preview. Native PortAudio output
was tested with a fake device; this host currently lacks `libportaudio2`, so its
native output-device latency is unverified. No serial device or physical actuator
was commanded. Physical timing and final composed-proposal validation remain at
the existing operator-approved hardware boundary.
