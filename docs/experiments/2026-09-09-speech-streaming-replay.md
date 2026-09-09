# Azelma speech and streaming-engine replay — 2026-09-09

## Question and scope

The operator selected Azelma from local Fantine, Éponine and Azelma auditions,
then requested a mouth synchronization test using the completed motion engine.
This experiment tests actual generated audio against that engine's software
timeline. No serial, camera, microphone or actuator was opened by this replay.
The operator had already confirmed browser audio playback worked.

## Reproduction and provenance

- Config: `config/speech/alice-sync-test.json`, seed 7.
- TTS: Pocket TTS 3.1.0, `english_2026-01`, CPU, offline cached model.
- Voice: Azelma, upstream VCTK p303 preset; selected by the operator. It is not a
  custom clone and no claim is made about speaker age. Source/license:
  [Kyutai voice collection](https://huggingface.co/kyutai/tts-voices/blob/main/README.md)
  and [VCTK corpus](https://datashare.ed.ac.uk/handle/10283/3443), CC BY 4.0.
- Expression: the same synthetic zero-residual fixture as
  `tests/motion/test_composed_streaming.py`; model seed 29, runtime seed 7,
  one-second lookahead, 0.4-second accepted prefixes, 5 Hz expression targets.
- Speech proposals: 50 Hz, 20 ms PCM windows, existing envelope and composition.
- Text/affect cues: assistant-authored for the operator's requested test. No
  participant recordings or learned expression training data are used.

```bash
HF_HUB_OFFLINE=1 uv run --extra speech python tests/motion/render_speech_replay.py \
  artifacts/speech/azelma-sync-reviewed
```

The output includes audio, previews, plans, composed traces, metrics and nested
manifests. `inputs/` retains the renderer, helper, imported baseline fixture,
affect schema/filter, speech plan and model configurations. Root checksums bind
all these files; nested manifests record package/model identity and code revision.
Use a new output directory for a repeat. Earlier audition and draft replay
outputs remain separate and are not overwritten.

## Results

The generated clip is **6.38 seconds**. The pre-review native run took 3.39 seconds
to prepare locally; final timings are recorded in each run's manifest. Speech
affect cues change throughout the actual clip.

The rebuilt `alice-speech:local` image also rendered the plan offline with no
network or devices in 8.65 seconds (`artifacts/speech/azelma-container-review/`).
Audition, native and container WAV hashes match:
`c7c196daec91fa457c431fedadb56290aeb0f475669f20723c7b0fc732e808de`.

Two expression replays use that same PCM:

| Mode | Intent/proposal outcomes | Accepted event records | Restart |
|---|---|---:|---|
| Current speech intent filter | 16 fallback intents; 16 fallback proposals | 0 | Exact equality |
| Neutral procedural fixture | 16 synthetic supported intents; 12 supported and 4 fallback proposals | 3 | Exact equality |

The current support set is empty and the learned residual weights are zero.
Changing speech affect therefore does **not** produce learned emotion movement.
The separately labeled procedural demo exercises the engine without representing
its synthetic supported intent as training evidence.

Integration tests verify that composition retains all 11 channels, changes only
the speech-owned jaw, follows PCM sample times, releases ownership at the end,
and reproduces accepted prefixes after restoring state. Browser logic is tested
with a Node fake DOM, including sparse channels and backward seeking. The preview
shows aperture plus numeric composed targets; it is not a physical face simulator.

Full suite: **718 passed**, two pre-existing fork deprecation warnings.
Ruff passed; mypy passed for 68 source files. Review found and resolved stale
values after backward seeking and incomplete replay-input provenance. No actuator
execution path was added.

## Physical result and conclusion

Physical mouth synchronization remains **unmeasured and not executed**. Raw jaw
targets span -1.0 to 0.59982 and finish at Home 0. With Home at rest one 20 ms
interval before playback, the initial -1.0 step implies command differences of
50/s and 2500/s². There are 177 speed intervals above the unfitted 2/s response
prior, including startup; 243 acceleration intervals exceed its 4/s² prior.
These are command-trace diagnostics, not observed mechanical quantities.

The existing settled-target Maestro adapter and identification runner cannot
serve as a speech executor without further integration. A physical test needs
bounded Home entry/exit, rate/acceleration shaping, jaw-only absolute limits,
clock/observed-motion instrumentation and a speech-specific reviewed procedure
with operator approval. Current electrical evidence covers initial ±0.05 jaw
identification only. See `hardware/speech-timing.md` for the reviewed handoff gaps.

Conclusion: Azelma's audio, mouth envelope and the completed streaming engine
interoperate in deterministic software replay. This does not validate trained
affect generation or physical lip synchronization.
