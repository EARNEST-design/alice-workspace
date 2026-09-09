# Speech and motion checkpoint — 2026-09-09

The operator ended the session after approving Alice's physical lip-sync test:
“Looks aligned” and “the test is great”. No further hardware run is requested
as part of saving this checkpoint.

## Resume here

- Worktree: `/home/alice/alice-workspace/.worktrees/streaming-affect-motion`
- Branch: `feature/streaming-affect-motion`
- Find the local checkpoint commit with `git log -1 --format='%h %s' -- SESSION_CHECKPOINT.md` in that worktree. Preserve any later edits/commits.
- Next plan: [Streaming speech and facial emotion integration](../superpowers/plans/2026-09-09-speech-emotion-integration-next-session.md).
- Main checkout has a `SESSION_CHECKPOINT.md` pointer. Older main-checkout ML handoff files remain preserved; their completed repair tasks should not be restarted.

## Accepted hardware baseline

| Setting | Accepted value |
| --- | --- |
| Local TTS | Pocket TTS 3.1.0, January English model, CPU |
| Voice | Azelma |
| Speech envelope | `config/speech/sync-hardware-v1.json`: gate 0.01, full-open RMS 0.06, attack 30 ms, release 60 ms |
| Mouth trajectory | `config/speech/jaw-speech-lead-v1.json` |
| Mouth calibration | Channel 6; 4608–5440 quarter-us; Home 5059; full -1…+1 |
| Controller response during speech | Speed 0 / acceleration 0 (unlimited firmware ramping) |
| Mouth timing | 100 ms aperture lookahead; emotion remains on current audio clock |
| Command limits | Step 0.4, rate 10/s, acceleration 200/s², 40 ms minimum interval |
| Normal completion | Confirm Home; restore runtime speed/acceleration 0/11 on serial owner; close |
| Fault/watchdog | Revoke and detach/close without restoration transactions; report non-restoration |

A correct target stream replaced per-target settling waits; audio runs
independently. Kinematics use host write-start timestamps, excluding readback
latency. A 2 ms dispatch window is enforced before sending. Never reinterpret
SENT as settled APPLIED or measured mechanics. The original settled adapter
and jaw-only scope remain available and tested.

Latest physical evidence: `artifacts/speech/azelma-lead100-camera-1/`.
Audio completed, Home confirmed, 166 targets (153 during speech), all immediate
PWM readbacks matched targets, 85/85 camera frames detected Alice, lip gap
7.27–37.14 px, total run 6.878 s. The operator accepted alignment. Camera timing
includes unknown buffering/exposure latency; it is not an exact acoustic lag
measurement. Settings exported before/after were identical; no EEPROM changes.
Runtime profile was restored after the trial. Power is under the operator's
master switch; do not infer power was removed or that the robot remains powered
on another day from this historical record.

## Reproduce the accepted trial when requested

Use a new output directory each time:

```bash
cd /home/alice/alice-workspace/.worktrees/streaming-affect-motion
uv run --extra speech alice-jaw-trial \
  --recording artifacts/speech/azelma-full-range-speech \
  --config config/speech/jaw-speech-lead-v1.json \
  --output artifacts/speech/resumed-mouth-trial \
  --enable-hardware --stream-targets --fast-jaw-response
```

Without the hardware flags the CLI verifies without devices. For generating
fresh speech, use `alice-speak` with `config/speech/alice-sync-test.json` and
`config/speech/sync-hardware-v1.json`. The calibrated test used a retained WAV;
true chunked TTS/LLM playback is the next milestone, not already implemented.

The robot-only camera observer is retained at
`artifacts/speech/jaw-camera-check/observe_trial.py` and copied into final trial
evidence. C525 stable path ends `C525_79C73260-video-index0`; C920 was not opened.
Its model is in the preserved `phase-1-passive-blendshapes` worktree. Camera uses
lip landmark gap and mouthUpperUpLeft/Right; nominal MediaPipe jawOpen is almost
zero on this robot and is not a reliable motion indicator.

## Existing emotion/motion work

The same branch already includes the repaired `ProductionCandidateComposer`
and `StreamingMotionGenerator`: persistent events, sparse accepted state,
restart consistency and model-package checks. Reuse them. The synthetic baseline
report is `docs/experiments/2026-09-09-streaming-baseline.md`.

All 11 servo names are mapped, and software speech composition preserves the
non-jaw expression channels. Only the mouth has been physically exercised by
this speech executor. Its fixed channel guard must not simply be deleted to
activate the face.

The current learned-support set and affect-anchor mappings are empty; replay
residual weights are zero. The engine's completion is not proof of a trained
emotion model. At next-session start inspect any newer model package, otherwise
label an authored/procedural expression demonstration honestly. The first new
integration should combine mouth with facial expressions; head gestures can
remain a later selectable addition.

## Validation and retained work

Before checkpointing, the final implementation passed 799 tests, Ruff and mypy
(73 source files), with two existing fork deprecation warnings. The final
checkpoint rerun log is retained in
`artifacts/checkpoints/speech-motion-2026-09-09/pytest.log`.
Independent reviews verified send-clock separation, fault cleanup, normal
response restoration and aperture-only lookahead. Review findings were fixed.

Key records: ADRs 0008/0009, `hardware/speech-timing.md`,
`hardware/bringup/mouth-speech-trial-v1.md`, and
`docs/experiments/2026-09-09-mouth-speech-trial.md`.
Ignored local artifacts include audio, model caches, camera frames, metrics,
source/config snapshots and feedback; they remain on this machine and are not
included in the Git commit. Preserve this worktree and its artifacts.

## Opening prompt for a new session

> Read SESSION_CHECKPOINT.md, then the September 9 speech-motion checkpoint and
> speech-emotion integration plan in the existing streaming-affect-motion
> worktree. Continue toward incremental LLM text/affect → local Pocket TTS PCM →
> DAC-clock expression generation and composed facial servo execution. Preserve
> the accepted Azelma voice, full-range mouth and 100 ms mouth lead. Reuse the
> existing motion engine, verify the actual expression-model support, and avoid
> restarting already completed ML repairs or repeating the old readiness ritual.
