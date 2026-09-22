# Conversation, Mouth and Expression Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Use subagent-driven-development when delegation is authorized in the next session. Steps use checkbox syntax. This is a saved next-session plan; do not begin hardware execution merely by reading it.

**Goal:** Make Alice's live English/Cantonese replies drive the mouth and selected facial expressions on the actual playback clock.

**Architecture:** The host conversation process commits bounded text and intended-delivery clauses. A local ROS gateway submits them to the existing RunSpeech session, whose TTS/audio/expression/motion/Maestro participants retain all playback and actuator authority. Both languages feed the same local sample timeline; the original audio-only output stays selectable for comparison.

**Tech Stack:** Existing Python 3.13 host, qualified ROS 2 Lyrical/Python 3.14 containers, httpx Unix-domain transport, rclpy, Pocket TTS, isolated CosyVoice process, NumPy, existing Pydantic contracts, pytest/Ruff/mypy.

**Spec:** [ADR 0020](../../architecture/0020-conversation-expression-and-small-decisions.md) and [handoff](../../checkpoints/2026-09-22-conversation-face-handoff.md). Read ADRs 0008–0012 before altering timing or actuator behavior.

## Global constraints

- Preserve the existing worktree and all local artifacts. Do not repeat completed September 9–15 streaming/ROS implementation tasks.
- Default hardware adapters to dry-run/mocked operation. This plan does not authorize new physical motion today. An attended run request in the next session authorizes its stated scope under AGENTS.md.
- One RunSpeech epoch, one PCM playback owner and one Maestro owner. Never play the same response through both pw-cat and ROS.
- Keep the accepted English Azelma and Cantonese `粤语女` speed 0.85/seed 7; 24 kHz mono and the existing 11572/32768 output cap. Hash model/source/profile identities.
- Keep the 200 ms initial prebuffer, two-second PCM capacity, 20 ms analysis/callback cadence and 100 ms mouth lookahead. A cue uses audible samples; the lead applies only to mouth aperture.
- Preserve original source timestamps, 250 ms active control/progress watchdogs, strict run identities and old-generation rejection. Model/network/serial work never runs inside the audio callback.
- The existing ROS response limit is ten seconds including its audio tail. Keep it for initial integration and use short replies; do not silently truncate a longer generated answer. Extending long-form speech is a separate budget review.
- Initial channels are jaw 6, then lids 3/4, forehead 5 and corners 9/11. Head, neck and gaze stay disabled. Never copy jaw's runtime 0/0 or dynamic limits onto other channels.
- Authored affect is Alice's intended delivery, not measured human emotion or a trained expressive model. Missing labels map to neutral.
- No raw microphone recordings, human transcripts, embeddings, credentials or downloaded weights in Git. Synthetic fixtures and derived metrics need provenance/config/seed/hash manifests.

## Task 1: Qualify a real ReSpeaker playback clock

**Files:** Read `src/alice/conversation/audio.py`, `src/alice/speech/stream_playback.py`, `pcm_stream.py`, `device_identity.py`, `infra/ros2/`, and accepted audio evidence. Modify `ros2_ws/src/alice_nodes/alice_nodes/audio.py` as well as the shared playback boundary; add `tests/speech/test_respeaker_playback.py` and `tests/ros2/test_respeaker_output.py`.

**Interface:** Extend `SoundDevicePlayback` with an optional explicitly verified output selector and output gain, retaining its default for existing callers. It still implements `PlaybackDriver(timeline, ring, emit, cancel, metrics)`. An injected stream factory is test-only at the dependency boundary, not a second production player.

ROS `AudioNode._device_play()` constructs OutputStream directly and does not call
SoundDevicePlayback. Factor shared verified stream-selection/gain setup into a
small production helper and call it from both paths. Bind its configuration in
the prepared profile. A library-only change cannot qualify the ROS route.

- [ ] Write a failing test that supplies a verified ReSpeaker selector and proves OutputStream receives it; an ambiguous/missing device must reject before opening any stream. Do not depend on an unstable numeric device index.
- [ ] Add a fake DAC test showing submitted samples lead played samples, the mouth queries `played + 2400` at 24 kHz, and delayed callbacks cannot fabricate played progress:

```python
# Use the existing fake callback helpers in test_pcm_stream.py.
# For 24 kHz, 100 ms is 2400 samples; never substitute generated_samples.
assert mouth_sample - played_sample == 2400
assert emotion_sample == played_sample
assert played_sample < queued_end_sample
```

- [ ] Run `uv run pytest tests/speech/test_pcm_stream.py tests/speech/test_stream_playback.py tests/speech/test_respeaker_playback.py -q`; observe the new selector/progress regression fail, then implement only the required route adapter.
- [ ] Exercise the existing speaker container overlay with synthetic PCM and no hardware mapping. Inspect actual stream callback/DAC progress and ReSpeaker routing. Confirm the output cap is applied once and mouth envelope calibration is derived from the same pre-gain PCM samples.
- [ ] In the actual ROS output test, pass synthetic `[1.0, -1.0, 0.0]` through the callback boundary and assert emitted samples are `[11572/32768, -11572/32768, 0.0]` within float tolerance. Verify device selection, no second multiplication, and an unchanged unscaled timeline envelope. Retain first-DAC/underflow failure regressions.
- [ ] If the selected ALSA/Pulse route cannot prove output progress, keep the audio-only benchmark working and report the failed route. Do not approximate timing from writes to pw-cat. A native PipeWire clock adapter is a separate measured fallback, with the same PlaybackDriver contract.
- [ ] Save route/config/code hashes, first-DAC time, queued/submitted/played counters, underflows and operator audibility in `docs/experiments/2026-09-22-conversation-face-integration.md` and ignored dated artifacts. Commit the tested playback-boundary change.

## Task 2: Commit reply text and delivery labels as clauses

**Files:** Create `src/alice/conversation/reply_clauses.py`, `config/speech/conversation-affect-v1.json`, `tests/conversation/test_reply_clauses.py`. Modify `models.py` and `runtime.py` behind an explicit expressive-output option; preserve plain-text reply mode.

**Interfaces:** New `ReplyClauseDecoder.feed(delta: str) -> list[SpeechClause]` and `finish() -> list[SpeechClause]`; constructor takes `generation_id`, `seed`, and an immutable authored mapping. Each newline-delimited object contains `text`, optional `delivery`, and `end_of_response`; decoder assigns unique clause IDs and monotonic sequences and validates with existing `ClauseSequence`. A final true flag is mandatory. Example wire data:

```json
{"text":"Hello, I'm glad to see you.","delivery":"positive","end_of_response":false}
{"text":"What would you like to try?","delivery":"neutral","end_of_response":true}
```

The initial authored map is neutral -> `(0,0,0), 0`; positive -> `(0.4,0.2,0), 0.5`; sympathetic -> `(-0.3,-0.1,0), 0.4`. Record these as authored policy values and preserve channel caps independently. Unknown/missing delivery uses neutral and emits a fallback reason. Invalid text/framing never becomes spoken metadata.

- [ ] Add failing cases for arbitrary SSE chunk boundaries, English/Cantonese punctuation, escaped JSON text, a partial final record, nonfinite/invalid values, too many clauses, duplicate final records and oversized text. A bounded record is <=4096 UTF-8 bytes; spoken text <=200 characters per clause and <=600 per response for this bench.
- [ ] Add an event-gated producer test: clause 1 is delivered while the producer withholds clause 2. Assert only `text` reaches TTS and no delivery label/control token is spoken.
- [ ] Implement the incremental parser and new opt-in Qwen reply prompt. Do not wait for a full JSON array or the entire answer, and do not revise already committed clauses. No separate model call is needed to classify Alice's own delivery in this first implementation.
- [ ] Test that late emotion metadata cannot alter a committed/audible clause, and missing delivery produces an honest neutral clause:

```python
decoder = ReplyClauseDecoder(generation_id="g1", seed=7, mapping=authored_mapping)
clauses = decoder.feed('{"text":"Hello.","end_of_response":true}\n')
assert clauses[0].vector == (0.0, 0.0, 0.0)
assert clauses[0].intensity == 0.0
assert clauses[0].end_of_response is True
assert decoder.finish() == []
```

- [ ] Run `uv run pytest tests/conversation/test_reply_clauses.py tests/conversation/test_models.py tests/speech/test_stream_contracts.py -q`; make the regressions green and commit the parser/policy. Prompt experiments use only synthetic text initially.

## Task 3: Bind bilingual voice profiles into ROS preparation

**Files:** Create `config/speech/conversation-profiles-v1.json`, `tests/ros2/test_conversation_profiles.py`. Modify `ros2_ws/src/alice_nodes/alice_nodes/base.py`, `tts.py`, `model_assets.py`, and reviewed files in `infra/ros2/`/`.dockerignore`; reuse `src/alice/conversation/external_tts.py` and `cosy_worker.py` rather than copying the synthesis implementation.

**Interfaces:** Add trusted `conversation-en-v1` and `conversation-yue-v1` selected profiles alongside `baseline` and `visible-face`. `effective_files(config_root, selected_profile)` must include the profile and pinned voice policy bytes in `config_digest`. Resolve immutable `language`/worker/model/source from the prepared profile, not from untrusted text or an unbound late parameter. One action uses one language. Existing SpeechClause and PCM wire formats can remain unchanged.

- [ ] Add failing tests for unknown profile, changed voice bytes after prepare, wrong sample rate, missing Cantonese worker and a language/profile mismatch. Defaults for old fixtures stay unchanged.
- [ ] Implement a common worker interface with `stream(clause)`, `cancel(generation_id)` and `close()`. English uses verified Pocket; Cantonese uses its isolated Python process and pinned model/source. No engine construction opens an audio or serial device.
- [ ] Extend the qualified container build/mount contract for the isolated Cantonese environment without mixing its Torch 2.8 dependencies into the ROS interpreter. Document source/image/license hashes and cache mounts. Warm before granting actuator authority; warm process identity remains bound until it exits.
- [ ] Test English -> Cantonese -> English with delayed first Cantonese PCM, cancellation during warmup and after the first chunk, worker exit and stale PCM. Assert one selected worker per admitted action, bounded queues and no orphan process.
- [ ] Run the new ROS tests in the qualified ROS test environment plus `tests/conversation/test_external_tts.py`, `test_cosy_worker.py` and `tests/speech/test_tts_worker.py` on their supported host environment. Record peak RSS/startup and commit the verified voice profile integration.

## Task 4: Bridge the conversation to one RunSpeech authority

**Files:** Create `src/alice/conversation/ros_speech.py`, `ros2_ws/src/alice_nodes/alice_nodes/conversation_gateway.py`, `tests/conversation/test_ros_speech.py`, `tests/ros2/test_conversation_gateway.py`; modify `runtime.py`, `cli.py`, ROS `session.py`, `audio.py`, `maestro.py` and lifecycle contracts/tests, the ROS package entry point and explicit Compose gateway overlay.

**Interfaces:** Host `RosSpeechOutput.speak_stream(clauses: AsyncIterable[SpeechClause], generation: str, *, language: str, audible: bool) -> dict[str, object]`, plus existing `warm()`, `stop()` and `close()` lifecycle methods. ROS owns `RunSpeech(source=COMMITTED_CLAUSES)` and `/alice/run/committed_clauses`; the Python 3.13 host never imports rclpy.

The sidecar gateway uses a private Unix socket in an owner-only runtime directory,
with bounded JSON requests: `POST /runs` prepares/adopts the authoritative epoch,
`POST /runs/{id}/clauses` commits exactly one clause with its original source time,
`DELETE /runs/{id}` cancels, and `GET /runs/{id}/events` streams bounded status.
No public TCP listener, transcript file logging or broad device mappings.

Startup must be designed explicitly: the current AudioNode starts a five-second
prebuffer timer at activation, while measured warm Cantonese first PCM took
8.864 seconds, before any new reply-generation time. Add a trusted PREPLAY state
under the admitted epoch: TTS and audio may generate/buffer while Maestro remains
prepared without device activation or motion authority. Use a profile-bound
30-second total PREPLAY budget for reply generation plus first PCM; expire/cancel
it without actuator writes. Initial prebuffer stays 200 ms and total queued PCM
stays <=2 s. A fresh validated PCM-ready barrier, not an elapsed sleep or invented
DAC timestamp, permits coordinated playback/actuator startup. Review the precise
barrier ordering against existing participant lifecycle tests before coding it.
Once the audio device starts, retain the 250 ms first-DAC and all active freshness
guards. Keep the baseline fixture profile's existing startup behavior unchanged.

- [ ] Add failing tests for a rejected goal, incorrect epoch/incarnation, duplicate/out-of-order clauses, >250 ms old source, a stalled gateway and a late result after Stop. Use synthetic clauses and fake action peers, not real devices.
- [ ] Implement startup so the gateway exposes an admitted epoch only after ROS preparation/roster validation; start reply generation after readiness rather than retaining old clauses through a long PREPARE. Each committed clause keeps the host source timestamp through the gateway; use the existing same-host clock proof, never a new remote clock assumption.
- [ ] Add event-gated regressions for 1.5 s reply delay plus 9 s first-Cantonese-PCM delay: PREPLAY remains valid with zero actuator access, then starts only after the barrier. A >30 s stall, Stop during PREPLAY, stale PCM-ready barrier or prior-run result must cancel without actuator access. Existing five-second behavior must first reproduce the failure. Parameterize a test clock to avoid long test sleeps; separately measure the real CosyVoice path.
- [ ] Refactor `_reply` to send a single response stream to expressive output. Do not reuse the current per-sentence `Speaker.clause(sequence=0, end_of_response=True)` behavior for a multiclause response.
- [ ] Keep audio-only Speaker selectable. Test that one response has exactly one playback owner and that Stop first revokes the run/PCM, then joins model/worker cleanup. One bounded run per response is sufficient; no always-active servo loop in this task.
- [ ] Run `uv run pytest tests/conversation/test_ros_speech.py tests/conversation/test_runtime.py -q` and ROS gateway/transport/lifecycle tests. Save a two-clause synthetic action trace showing one epoch and one terminal outcome, then commit the bridge.

## Task 5: Compose and display output on the audible clock

**Files:** Reuse `src/alice/speech/expression_bridge.py`, `composer.py`, `face_runtime.py`, `face_stream.py`; extend only when a regression demonstrates a gap. Modify `conversation/events.py`, `src/alice/resources/conversation-dashboard.html`; add `tests/conversation/test_expression_status.py` and extend `tests/speech/test_expression_bridge.py`, `tests/ros2/test_conversation_gateway.py`.

**Interfaces:** Gateway feedback carries existing generation/epoch, queued/submitted/played samples and clock kind, plus authored delivery cue and active channel set. Derived `expression`/`motion` dashboard events are observations, not actuator commands. Keep proposed target, SENT receipt, observed PWM and mechanical evidence separately labeled.

- [ ] Before implementation, test a future positive clause fully synthesized ahead of playback. Assert the expression stays neutral until that clause's audible boundary; allow only the documented next accepted-prefix transition delay (<=400 ms). Speech mouth uses the existing led envelope while lids continue an already accepted blink.
- [ ] Test delayed TTS, PCM starvation, serial/watchdog fault, mid-clause Stop and immediate new generation. Assert no old PCM or pose reaches the new run and no fault path sends Home/restoration.
- [ ] Add the dashboard fields without exposing raw model internals as user decisions: current delivery, voice, audio phase, queued/played time, mouth lead, motion mode and fault reason. Show simulated versus physical mode prominently.
- [ ] Use the existing provisional 3D description only as a preview of composed proposals. Do not infer calibration from its geometry or count a preview as physical acceptance.
- [ ] Run `uv run pytest tests/conversation tests/speech tests/motion/test_speech_streaming.py -q` and the affected ROS suite. Save bounded synthetic replay artifacts and commit the integration/status change after review.

## Task 6: Attended physical qualification and handoff

**Files:** Update the experiment report, `hardware/bringup/face-speech-trial-v1.md` and session checkpoint; create a dated immutable trial config and ignored artifact manifest. Qualifying hardware admission requires a reviewed ownership design in `docs/architecture/`, a narrowly scoped verifier under `src/alice/hardware/`, and tests in `tests/hardware/` plus `tests/ros2/test_final_repairs.py`. Modify ROS `base.py` only after that verifier qualifies.

**Interfaces:** Existing `FaceRuntime`/`FaceCommandStream` remains the actuator boundary. Select a verified transport for Maestro serial `00037376`. Native USB sweep evidence does not automatically qualify the existing compact-serial face adapter; current fixed-9600 UART has no readback on the external adapter.

**Present hard blocker:** `require_ros_hardware_visibility()` in ROS `base.py`
unconditionally rejects hardware PREPARE. No complete host FD visibility verifier
is qualified. This is separate from transport readiness; neither a successful
sweep nor `pid: host` nor empty fuser output removes it. Keep this rejection in
place until the independent ownership task passes. Simulation/audio-only work
can proceed meanwhile.

- [ ] Reconcile current read-only controller identity, configuration and ownership with September 18/22 evidence. Preserve saved controller settings. If the guarded adapter cannot communicate, qualify native USB behind the same receipts/fault interface or resolve the specific transport problem; do not flash/change mode as an incidental step.
- [ ] Design and test a complete-host ownership verifier for both Maestro interfaces 00/02 and the native USB route. Its attestation binds device identity, run/epoch, requester, inspected namespaces and freshness; unreadable process/FD tables, stale/malformed proof, hidden or competing owner and ambiguous USB ownership must reject. Test an owner outside the requesting container and a competing raw-USB owner. Record the required minimal host privileges explicitly; do not grant broad container privilege as a shortcut. If complete evidence cannot be established, leave physical ROS admission blocked and document that precise next step.
- [ ] Complete three device-free cases first: English positive -> neutral, Cantonese sympathetic -> neutral, then English/Cantonese/English consecutive runs. Require zero stale-generation writes, zero underflows and bounded cancellation.
- [ ] On the operator's attended run request, execute a short jaw-only phrase. Compare with the accepted 100 ms lead baseline and ask for timing/audibility feedback. Keep exact channel limits and automatic fault stops.
- [ ] After jaw acceptance, run the selected facial set with contrasting authored clauses. Record audible cue times, proposed/sent/PWM times, watchdog health and operator-visible expression quality; use robot-facing camera only if recording is authorized. PWM readback is not measured mechanical arrival.
- [ ] Verify Stop during speech, healthy successful Home/restoration, and language switching in separate bounded trials. Fault/cancel paths must retain their existing no-restoration behavior.
- [ ] Record latency distributions, maximum RSS, every failure, selected profile/model/code hashes and feedback. Update the handoff with actual completed checkboxes. Do not mark complete until the operator accepts combined mouth and expression motion with speech.

## First useful stopping point

Finish Tasks 1–4 with real bilingual PCM and simulated servos. That yields a
reviewable connection between the current conversation and existing synchronized
runtime without claiming physical expression acceptance. The dedicated admission
model plan can progress independently; do not mix a new classifier and first
physical motion into one uncontrolled comparison.
