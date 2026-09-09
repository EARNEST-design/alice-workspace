# Streaming Speech and Facial Emotion Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Use subagent-driven-development only if the next session authorizes delegation. Steps use checkbox syntax. This session ends at a checkpoint; do not start implementation merely by reading this handoff.

**Goal:** Let Alice speak incrementally in Azelma's voice while her mouth and other facial expressions follow the same audible speech, preserving the accepted mouth timing.

**Architecture:** An asyncio coordinator accepts committed text/affect clauses, a dedicated warm Pocket TTS process emits PCM chunks, and a bounded playback buffer supplies a DAC sample clock. The existing motion generator produces expression proposals on that clock; speech owns mouth aperture with 100 ms lookahead. One serial owner validates and sends the composed targets for an explicit set of enabled channels.

**Tech Stack:** Existing Python/uv environment, Pocket TTS 3.1.0 (`english_2026-01`, CPU, Azelma), NumPy, PyTorch, Pydantic, sounddevice/PortAudio, pyserial, pytest, Ruff, mypy. Use asyncio and a spawned process initially; ROS 2 is optional later transport, not a prerequisite.

**Spec:** [ADR 0009](../../architecture/0009-incremental-speech-and-affect-streaming.md), [ADR 0008](../../architecture/0008-mouth-only-speech-trials.md), and [session checkpoint](../../checkpoints/2026-09-09-speech-motion.md).

## Global constraints

- Resume `/home/alice/alice-workspace/.worktrees/streaming-affect-motion`, branch `feature/streaming-affect-motion`. Preserve newer work; never reset to a historical checkpoint.
- User accepted the real mouth trial: Azelma, full calibrated channel 6 range 4608–5440 quarter-us around Home 5059, controller runtime speed/acceleration 0/0, and 100 ms mouth lead.
- Preserve `jaw-speech-lead-v1.json` and `sync-hardware-v1.json` as the comparison baseline. Jaw command caps are step 0.4, rate 10/s, acceleration 200/s², minimum interval 40 ms. These are not settings for every other servo.
- Default devices remain mocked. For attended physical trials, follow the operator's standing setup in AGENTS.md: a direct run request authorizes the stated scope; do not resurrect RUN/OFF phrases or repeated readiness checklists.
- The next session's combined-motion scope is mouth plus facial expressions. Keep head/neck motion separately selectable; a facial-expression test does not require a head gesture.
- One serial owner, one audio callback, bounded queues. Model inference and serial I/O must not run inside the audio callback or block the asyncio loop.
- Preserve requested targets, host send timestamps, current PWM observations, and camera measurements as separate facts. SENT is not APPLIED or mechanical arrival.
- Fault/watchdog close sends no restoration transactions. Normal Home completion restores the jaw runtime 0/11 profile on the serial-owner thread. No EEPROM writes are needed.
- Model loading never opens devices. Version model/config identities, support provenance, seeds, source text and artifact checksums. Keep raw imagery/audio and credentials out of commits.
- Mouth lookahead does not shift emotion, expression ownership or the audible sample clock. Never retime played audio/cues using a later final sentence duration.

## Current state: reuse these components

| Existing path | Responsibility |
| --- | --- |
| `src/alice/speech/synthesis.py` | Local Pocket TTS, voice/model caching; currently returns complete PCM |
| `src/alice/contracts/speech.py`, `speech/timeline.py` | Bounded offline plan, audio spans, mouth envelope, affect frames |
| `src/alice/speech/playback.py` | PortAudio playback clock and cancellation |
| `src/alice/speech/composer.py` | Sparse expression accumulation and speech jaw ownership |
| `src/alice/speech/jaw_playback.py`, `jaw_trial.py`, `streaming_jaw.py` | Accepted mouth hardware trial, lookahead, bounded trajectory and sent receipts |
| `src/alice/motion/streaming.py` | `ProductionCandidateComposer`, `StreamingMotionGenerator.replan`, accepted-prefix continuation |
| `src/alice/motion/state.py`, `intent_filter.py` | Persistent state, accepted intent and fallback |
| `src/alice/models/package.py` | Model/package compatibility, weights and provenance validation |
| `src/alice/hardware/maestro_adapter.py`, `safety/supervisor.py` | Device boundary and supervised lifecycle |
| `tests/motion/test_speech_streaming.py`, `speech_replay_helpers.py` | Existing speech/expression integration reference; test fixtures, not runtime factories |
| `hardware/alice-face-v1.yaml` | All 11 semantic servo mappings |

The motion repair tasks from the older September 7 handoff are already complete.
Read `docs/experiments/2026-09-09-streaming-baseline.md` instead of repeating them.
The checked-in support set (`config/affect/intent-filter-v1.yaml`) and affect-to-anchor mappings (`config/models/procedural-motion-v1.yaml`) are empty. The speech replay's residual weights are explicitly zero. The engine works, but those fixtures do not establish trained emotional expression. A newer external package must be checked, not assumed.

## Task 1: Rebaseline and resolve the expression source

**Files:** Read the checkpoint, `AGENTS.md`, `agents/systems-architect.md`, `agents/emotion-ml.md`, `agents/motion-control.md`, package/config files above, and the baseline report. Record findings in `docs/experiments/2026-09-09-speech-emotion-integration.md` when execution begins.

**Interfaces:** Existing `load_package(path, expected_identities)` and production composer; produces an explicit expression source and its identity/provenance.

- [ ] Run `git status --short`, `git log -8 --oneline`, `uv run pytest -q`, `uv run ruff check src tests`, and `uv run mypy src/alice` in the existing worktree. Compare with the checkpoint's 799-test baseline.
- [ ] Inspect newer task/workspace artifacts for a real fitted model package. Load through the existing verified package API; record package digest, training provenance, support coordinates and evaluation status.
- [ ] If no fitted package exists, retain honest neutral/fallback behavior in learned mode. For a visible integration demonstration, use an explicitly labeled authored-expression policy with configured smile/frown/neutral anchors and procedural blinks. Do not add invented training coordinates or relabel zero weights as trained emotion.
- [ ] Record the selected demonstration source before implementation. The initial delivery is integration; training a new model family is a separate milestone.

## Task 2: Commit incremental text and affect clauses

**Create:** `src/alice/contracts/speech_stream.py`, `tests/speech/test_stream_contracts.py`.
**Modify:** `src/alice/speech/cli.py` only when the coordinator is ready.

**Interfaces:** Define `SpeechClause` with `schema_version='speech-clause/v1'`, `generation_id: str`, `clause_id: str`, `sequence: int`, `text: str`, `vector: tuple[float,float,float]`, `intensity: float`, `seed: int`, and `end_of_response: bool=False`. Reuse existing affect ranges/text bounds. Input is `AsyncIterable[SpeechClause]`; no provider-specific SDK belongs in the core contracts.

- [ ] Write validation tests for blank text, nonfinite/out-of-range affect, invalid seeds, duplicate sequence IDs and attempts to replace a committed clause.
- [ ] Make a JSON-lines fixture with two short clauses and contrasting affect. A fixture async source must yield clause 1 while withholding clause 2 until the consumer explicitly requests it.
- [ ] Run `uv run pytest tests/speech/test_stream_contracts.py -q` and observe missing-contract failures; then implement the immutable event model and coordinator sequence checks.
- [ ] Preserve the offline fractional-cue plan. Live v1 cues apply at clause audio onset; optional later alignment may add sample-anchored cues. Do not wait for whole-response JSON or calculate live cue times from unknown final duration.
- [ ] Verify focused tests and commit `feat: define incremental speech and affect clauses`.

## Task 3: Stream Pocket TTS from one warm process

**Create:** `src/alice/speech/tts_worker.py`, `tests/speech/test_tts_worker.py`.
**Modify:** `src/alice/speech/synthesis.py` to share model/voice setup without changing its offline API.

**Interfaces:** Define `PcmChunk(generation_id, clause_id, sequence, sample_rate, pcm, final)` as an immutable internal message; mono finite float32 PCM, chunk order checked by the receiver. `PocketTtsWorker.stream(clause: SpeechClause) -> AsyncIterator[PcmChunk]` is the coordinator-facing API. `cancel(generation_id)` invalidates queued results; `close()` joins/terminates the owned process within a bounded deadline.

- [ ] Write a fake chunk generator that yields two chunks then waits on a test event. Assert the first chunk reaches the consumer before that event is released. Assert generation IDs reject stale chunks after cancellation and message capacity applies backpressure.
- [ ] Inspect the installed `pocket_tts/models/tts_model.py` signature for `generate_audio_stream` and its copied voice-state semantics. Do not guess an API from a different package version.
- [ ] Use `multiprocessing.get_context('spawn')` so the worker cannot inherit a live serial descriptor. Load January English/Azelma once per process, set the bounded CPU thread count inside it, and stream yielded arrays through a bounded IPC channel. Keep Torch globals/RNG separate from emotion inference.
- [ ] Keep the model call serialized. Cancellation drops old-generation chunks immediately; if native generation cannot be interrupted promptly, bound shutdown and restart the owned worker rather than leaking it.
- [ ] Test chunk-before-final delivery, finite PCM/rate validation, cold/warm deterministic seeds, cancellation, worker failure and shutdown. Run `uv run pytest tests/speech/test_tts_worker.py tests/speech/test_synthesis.py -q`.
- [ ] Commit `feat: stream local Azelma PCM from an isolated worker`.

## Task 4: Play a bounded PCM stream and derive mouth timing

**Create:** `src/alice/speech/pcm_stream.py`, `src/alice/speech/stream_session.py`, `tests/speech/test_pcm_stream.py`, `tests/speech/test_stream_session.py`.
**Modify:** `speech/playback.py`, `speech/timeline.py`, `speech/jaw_playback.py` through explicit interfaces; retain offline replay.

**Interfaces:** `PcmTimeline.append(chunk)` assigns contiguous absolute sample offsets and records clause-onset cues. `frame_at(sample_index) -> SpeechFrame` reads committed envelope/affect state. `played_sample` tracks the actual DAC clock separately from generated/queued counts. `SpeechStreamSession.run(clauses: AsyncIterable[SpeechClause], cancel: asyncio.Event) -> dict[str, object]` records the run outcome and metrics.

- [ ] Write boundary tests before implementation: feeding identical PCM as one array or irregular chunks must produce the same 20 ms RMS/envelope frames. Preserve incomplete windows and attack/release state between chunks; retain fixed RMS calibration 0.01 gate/0.06 full open.
- [ ] Implement a bounded ring buffer with an initial target of 200 ms queued PCM, enough for 100 ms mouth lead plus output scheduling margin. Account for generated, queued and DAC-played samples separately. Record buffer depth and actual first-audible latency.
- [ ] The callback copies available PCM only. Precompute envelope/cues outside it. At audible sample `s`, mouth reads aperture at `s + round(0.1 * rate)`; affect and ownership read at `s`. Do not move the face merely because a future clause has been generated.
- [ ] Define starvation explicitly: pending deliberate silence is represented in the sample timeline with closed aperture; a device underflow aborts the utterance. Never replay a stale mouth frame or treat zero-filled accidental underflow as successful playback.
- [ ] Test the key overlap with synchronization events, not sleeps:

```python
# Fake producer yields initial PCM and then blocks before its final chunk.
# Fake LLM source withholds its final clause at a separate event.
# Assert playback's first audible callback happens while BOTH events remain unset.
# Then release them, finish playback, and assert one terminal ownership release.
```

- [ ] Test cancellation across all three queues, no stale-generation PCM/targets, bounded memory with a stalled consumer, short utterances smaller than the prebuffer, clip tail/closure, and worker/device failure propagation. Include the existing lead and playback tests.
- [ ] Commit `feat: play incremental speech on a buffered DAC timeline`.

## Task 5: Compose live emotion motion with speech

**Create:** `src/alice/speech/expression_bridge.py`, `tests/speech/test_expression_bridge.py`, and an explicit integration fixture/config.
**Modify:** `speech/composer.py`, `motion/streaming.py` only where needed; promote reusable runtime construction from the existing test fixture into production code instead of importing tests at runtime.

**Interfaces:** `ExpressionBridge.advance(frame: SpeechFrame, played_sample: int, sample_rate: int) -> TargetUpdate` maintains a persistent `GeneratorState`, calls `StreamingMotionGenerator.replan(...)` only when another accepted prefix is needed, and returns the expression at the audible position. Feed that proposal into existing `compose_frame` using the led mouth aperture and current speech ownership.

- [ ] Bind the generation's sample origin to one monotonic clock. Preserve accepted-prefix state, RNG, event phases and complete sparse pose across clauses; a replan must not restart a blink/gesture or advance acceptance through unplayed speculative time.
- [ ] Add a two-clause regression where clause 2 is fully generated ahead of playback. Assert its affect does not appear before its audible sample boundary and does appear at/after it.
- [ ] Test simultaneous speech and an accepted blink: mouth follows the speech aperture; eyelids retain the blink phase; mouth corners/forehead/eyes retain their expression targets. Current speech weight governs release back to baseline.
- [ ] Test an emotion transition during an existing event, supported versus fallback intent, restored-state replay, cancellation during lookahead, and stale-source handling. Keep authored demonstrations distinctly identified if Task 1 found no fitted model.
- [ ] Render one retained composed replay before physical execution. Record model/config/seed identities, all 11 channel proposals, audio samples and per-channel derivative bounds. Compare the mouth trace against the accepted hardware profile.
- [ ] Run the expression bridge tests and `tests/motion/test_speech_streaming.py`, `test_streaming.py`, `test_composed_streaming.py`; commit `feat: compose speech and expressions on the playback clock`.

## Task 6: Execute mouth plus selected facial servos

**Create:** `src/alice/speech/face_stream.py`, `src/alice/experiments/face_speech_cli.py`, `tests/speech/test_face_stream.py`, `hardware/bringup/face-speech-trial-v1.md`.
**Modify:** Hardware adapter/protocol only through a separate explicit multi-channel interface; leave the fixed jaw-only adapter guard and regression runner intact.

**Interfaces:** One trusted face-stream executor consumes composed `TargetUpdate` values and a fixed allowlist drawn from `hardware/alice-face-v1.yaml`. Each sent receipt retains per-channel target, host send time and current PWM. No unchecked model output can write directly to serial.

- [ ] Define an initial facial allowlist using mouth, mouth corners, forehead and eyelids from the actual mapped proposal; enable eye gaze deliberately if included. Keep neck_rotation/head_tilt/face_pitch out of the initial facial trial. Document exact selected channels and caps before a physical run.
- [ ] Add disconnected tests proving unselected channels never emit bytes, sparse updates preserve existing selected values, coupled eyelid/corner updates compose correctly, and a variable-latency transaction does not corrupt per-channel command timing.
- [ ] Preserve one serial owner and latest-target coalescing. Do not apply mouth's 0/0 runtime tuning or full-range dynamic caps to all channels. Derive selected-channel limits from their actual calibration/profile and the reviewed proposal.
- [ ] Test cancellation/stale source/controller error/partial serial write while audio is active. Fault closure must perform no extra transactions; normal completion must confirm Home for enabled channels before restoring the jaw response profile.
- [ ] Run a device-free composed trial, then the operator-requested attended physical trial. No repeated readiness phrases are needed. Reuse the robot-facing C525 observer only; do not open the human-facing C920 or a microphone.
- [ ] Record full configs, code/model/input hashes, queue/audio metrics, sent targets/current PWM, robot-camera evidence and operator feedback. Confirm mouth alignment remains acceptable while other expressions move. Commit `feat: execute composed speech and facial motion` after review and checks.

## Completion criteria for the next milestone

- [ ] First audio plays before the LLM finishes and before first-clause TTS finishes.
- [ ] Mouth keeps the accepted 100 ms compensation, including chunk/clause boundaries.
- [ ] Expressions visibly coexist with speech and transition on the audible clause clock.
- [ ] The selected expression source is honestly labeled and its support/model provenance retained.
- [ ] Audio stays independent of model/serial work; cancellation flushes old-generation work; queues remain bounded.
- [ ] Selected facial channels pass their own limits and physical trial, with head motion separate.
- [ ] Tests, type/lint checks, a retained experiment report and the next checkpoint exist.

If time is limited, finish Tasks 1–5 with real incremental audio and simulated composed servos, then leave Task 6 as the explicit next hardware step. Do not declare full integration complete from a static preview or full-WAV replay.
