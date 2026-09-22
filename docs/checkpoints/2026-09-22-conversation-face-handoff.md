# Next-session handoff: conversation, expressive speech and small decisions

Date: 2026-09-22. Start here, then read `SESSION_CHECKPOINT.md` and AGENTS.md.
The user requested GitHub synchronization and a plan, not implementation or servo
motion in this handoff session.

## Resume the correct branch

```bash
cd /home/alice/alice-workspace/.worktrees/streaming-affect-motion
git status --short
git log -5 --oneline
```

Implementation branch: `feature/streaming-affect-motion` in
`EARNEST-design/alice-workspace`. Preserve this worktree, the live service and all
ignored local artifacts. Do not reset to an older checkpoint. Main is a planning
entry point; the working speech/ROS implementation is on this feature branch.
Other retained branches: `feature/phase-1-passive-blendshapes`,
`feature/phase-2-actuator-identification`, and `codex/robot-description`.

## What works now

| Component | Current state |
| --- | --- |
| ReSpeaker Lite | Official XMOS USB audio 2.0.7; input and Linux speaker output work. XIAO I2S mode was replaced; restore image remains local. |
| Capture/VAD | Local 16 kHz, two processed identical input channels; Silero 6.2.2, 300 ms silence, 200 ms pre-roll. These are not separate people. |
| ASR | Hosted Qwen ASR at `https://work.manakin-gecko.ts.net:10000/v1`; English/Cantonese. Recognition and language confidence are unavailable, retained as unknown. |
| Admission | Temporary remote `qwen3.8-27b-mlx` at `http://earnests-mac-studio:1234`; strict SPEAK/WAIT, eight-second maximum, errors wait. |
| Wake | “Alice”, “愛麗絲”, “爱丽丝” at the start of a final transcript; audio/model checks before opening a 30 s follow-up window. Both UI modes require wake outside that window. |
| Reply | Same remote Qwen, streamed short spoken replies. Current bench deliberately bounds reply length. |
| English voice | Local Pocket TTS 3.1.0, accepted female Azelma. |
| Cantonese voice | Local CosyVoice-300M-SFT `粤语女`, speed 0.85, seed 7, OpenCC preprocessing; operator accepted voice, pace and volume. |
| Playback | Native PipeWire `pw-cat`, verified ReSpeaker sink, 24 kHz mono float32 with existing digital output cap. No live mouth/face link yet. |
| Conversation dashboard | `http://127.0.0.1:8765/`; bounded five-minute sessions; Stop cancels the current turn. Both voices warm before capture. |
| ROS observer | Existing device-free `alice-conversation-observer`, domain 74, local-only discovery. It reports events; it does not command servos. |
| Overlap | Pinned local pyannote segmentation ONNX; wait at >=200 ms overlap, <120 ms speech or analysis failure. No persistent person identity. |

The last deployed service execution session is 48265; its log is
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/bench-service-decision-timeout-20260922.log`.
It was left in bounded wake listening after the timeout repair. Recheck actual
state; this statement does not mean a five-minute session remains active forever.
Use the exact launch command and accepted Cantonese flags in
[the runbook](../conversation-bench.md). Do not start duplicate dashboard/observer
processes. The service starts idle after restart.

## Important recent findings

- The operator heard a live answer after retrying a recognized wake. The prior
  silent turn was an admission timeout, not a speaker failure. Same-request probes
  later took 1.1–1.4 s. Raising 3 s to 8 s adds tolerance; the original server-side
  slowdown was not identified. Ninety focused tests passed after that repair.
- The previous language-switch freeze was capture overflow caused by waiting for
  TTS cancellation while capture stopped draining. Default reply guarding now
  covers generation and playback. Keep that fix and both-voice warmup.
- MiniCPM is unloaded and Kev is stopped. Idle CosyVoice uses about 4.8 GiB RSS,
  English about 0.9 GiB. Stop retains warm workers; a graceful service restart
  releases them. Linux available RAM was ~22 GiB after release, ~16 GiB with both
  voices active, with zero current memory-pressure averages during checks.
- Mac Studio advertises both Qwen and GPT-OSS 120B loaded. Do not unload an unrelated
  workload or assume capacity for offloading. Cantonese TTS is the largest useful
  candidate if local pressure recurs; microphone/playback/control stay local.
- The speech probability meter estimates speech presence, not ASR confidence.
  Long uninterrupted >15 s input is discarded once, then rearms after 320 ms quiet.

## Decision-model direction: user preference is explicit

The user wants a smaller dedicated decision model, **Kev or original Jev**, and
Qwen reserved for substantive answers. Treat current Qwen admission as temporary.
Do not silently equate Jev with a Kev compatibility alias.

Prior small synthetic screens: Kev 0.6B, 0.8B and tested Qwen3 4B int8 each 8/14;
4B missed five of six valid calls and worst long Cantonese latency was ~32.7 s.
MiniCPM scored 6/12 with full evidence, effectively always SPEAK. Qwen passed that
separate 12-case screen, but an eight-pair oracle-speaker-context test still had
a peer-answer false activation. These are limited, different screens, not broad
accuracy estimates. Preserve the negative results and test revisions.

The [small-decision plan](../superpowers/plans/2026-09-22-small-speech-admission.md)
starts with compact context and atomic typed questions, then apples-to-apples
evaluation including option-order and precision checks. Jev's documented hosted
API needs an API key; no credential has been requested or discovered for this plan.
Begin with synthetic inputs, not uploaded human room recordings.

## Diart status

Installed isolated local CPU trial, not active in the conversation loop. About
0.6 GiB RSS, typical 90–165 ms computation per 500 ms update, plus buffering delay.
Speaker category consistency looked useful, but new-speaker onset coverage missed
the frozen acceptance gate, even with 1 s latency. No reliable person identification
or Cantonese room qualification is claimed. Shadow-only integration is the next
perception step; never silently label unknown/stale/mixed segments as the addressee.
See [ADR 0019](../architecture/0019-local-diart-trial.md).

## Existing motion foundations to reuse

- `src/alice/speech/pcm_stream.py`, `stream_session.py`, `stream_playback.py`:
  bounded PCM, played-sample clock, 200 ms prebuffer, 2 s ring, 100 ms mouth lead.
- `expression_bridge.py`, `composer.py`: clause affect at audible time; persistent
  authored/procedural expression; speech owns jaw. Accepted-prefix changes may lag
  a cue by up to 400 ms. Do not claim instantaneous expression onset.
- `face_runtime.py`, `face_stream.py`, `face_adapter.py`: single serial owner,
  selected-channel caps, independent 250 ms watchdog, fault/cancel cleanup.
- `ros2_ws/src/alice_nodes/alice_nodes/`: eight participant runtime, immutable
  preparation/roster/config identities, RunSpeech action and committed clauses.
  `infra/ros2/` supplies the qualified container setup.
- ROS physical admission is deliberately blocked: `base.py`'s
  `require_ros_hardware_visibility()` always rejects until complete host FD/device
  ownership can be proven. A new gateway cannot bypass this. Also, AudioNode's
  current five-second prebuffer deadline is too short for the measured 8.864 s
  warm Cantonese first PCM plus a reply request. The plan includes a bounded
  PREPLAY phase with no actuator authority, then the existing first-DAC/active
  watchdogs. The ROS output boundary needs its own route/gain change; it does not
  use the host SoundDevicePlayback class.
- Initial selected face channels: lids 3/4, forehead 5, jaw 6, corners 9/11.
  Keep neck/head/gaze outside the first composed trial. Jaw's accepted full range
  4608–5440 qus, Home 5059, 100 ms lead and runtime 0/0 are jaw-specific.
- There is **no qualified fitted emotion package**. Empty support coordinates and
  zero residual fixtures remain honest fallback/software checks. Use labeled
  authored neutral/positive/sympathetic delivery for the first visible integration.

Hardware evidence must be reconciled before using the old serial transport:
the September 18 UART setup is fixed 9600 with no adapter reply bytes; September 22
native USB completed an 11-channel 60% sweep and restored settings/Home. This does
not qualify the old serial adapter or prove mechanical expression quality. Read
`hardware/maestro-direct-uart-evidence-2026-09-18.md` and
`hardware/bringup/2026-09-22-sixty-percent-sweep.md`. Recheck identity/config read-only;
choose a verified transport without silently changing persistent controller mode.

## Next session: sequence and stopping points

1. Rebaseline and qualify a timestamped ReSpeaker playback route without servo
   access. The current conversation `pw-cat` process does not expose the qualified
   DAC sample clock needed for mouth sync.
2. Follow [speech/face integration](../superpowers/plans/2026-09-22-conversation-face-integration.md):
   structured committed reply clauses, bilingual ROS TTS profiles, one RunSpeech
   authority, shared DAC timeline, dashboard feedback and cancellation.
3. Prove English/Cantonese/English, delayed TTS, Stop and stale-result cases with
   simulated servos. Then attended jaw-only, then selected face. A user request to
   run authorizes the stated scope under AGENTS.md; do not repeat readiness rituals.
4. Independently evaluate the dedicated decision path. Promote only a passing
   candidate; neutral expressions/WAIT remain explicit failure behavior.

Completion means audible speech, mouth timing and visibly matching authored
expression accepted together by the operator, with bounded cancellation and no
old-generation motion. Changing PWM alone is not physical acceptance. Preview/3D
motion is a useful comparison, not proof of actuator behavior.

## Publication and provenance

GitHub receives source, tests, dependency locks, reviewed hardware/experiment notes,
architecture decisions and these handoff plans. `artifacts/`, downloaded weights,
private model environments, raw recordings, credentials and generated embeddings
remain ignored and local. Their locations/hashes and conclusions are documented;
“synced” does not imply those local assets are backed up to GitHub.

Fresh publication checks and branch synchronization are recorded in
`docs/experiments/2026-09-22-github-handoff.md`. The main-checkout checkpoint points
here. No motion or new backend deployment was performed while preparing this handoff.
