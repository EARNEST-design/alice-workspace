# Local conversation bench and live dashboard

Date: 2026-09-21. Status: accepted with the operator's hosted Qwen ASR substitution on 2026-09-21; implementation in progress.
Baseline: `feature/streaming-affect-motion`, `07fc60b`; preserve local notes and
experiment artifacts. English first is the operator's selected test language.

## Outcome

An attended, audio-only bench session shows the actual chain in a local browser:
ReSpeaker microphone → Silero VAD → hosted Qwen ASR → MiniCPM speak/wait advice →
remote LM Studio streaming reply → Pocket TTS/Azelma → Linux speaker output.
ROS carries observation events so the same session can be inspected with ROS
tools. The first milestone does not change the reviewed ROS actuator runtime.

The default launch is idle. Start listening enables a bounded five-minute
microphone session; Stop closes capture, cancels requests and stops playback.
Listen-only and conversation modes are explicit. No raw microphone files are
saved. Transcripts and context remain in bounded process memory and the local
browser; exported experiment metrics omit operator words.

## Approaches and decision proposed

1. **Host bench service plus a ROS telemetry sidecar (recommended).** Uses the
   working Linux/PipeWire path and isolates new inference dependencies. Fastest
   route to measuring this hardware and both models. ROS observation is real,
   but it does not yet own speech execution or synchronized face animation.
2. Integrate immediately into the eight-node ROS `RunSpeech` action. Reuses
   synchronized speech/expression execution, but requires changes to per-response
   preparation, ten-second limits, clause admission and cancellation lifecycle.
3. Add Pipecat and its ROS adapter now. Provides useful conversation abstractions,
   but adds dependency and transport qualification before the requested bench.

Choose option 1 for this experiment. Keep the earlier unaccepted ADR 0014 as
the broader ROS integration proposal; do not silently mark it implemented.
Use measured results to decide the permanent integration later.

## Components and contracts

- A host Python service owns capture, VAD and bounded utterance buffers. Capture
  callbacks only copy frames and update counters; model inference and HTTP never
  run on the audio callback. Select the stable ReSpeaker node name and verify
  `2886:0019`, serial `0000000001`. Input is 16 kHz; the current two channels
  contain the same processed mono signal. Never choose a monitor as microphone.
- Hosted ASR uses `https://work.manakin-gecko.ts.net:10000/v1`, model
  `qwen3-asr` (`Qwen/Qwen3-ASR-1.7B`, vLLM 0.14.0). Upload bounded 16 kHz mono
  WAV segments to `/audio/transcriptions`, language `en`, temperature 0, seed 7,
  `stream=true`, with TLS verification. Consume SSE transcript deltas, remove
  Qwen's language/ASR framing, require successful stream completion and reject
  truncated/error responses. No automatic retry or local fallback sends duplicate
  turns. The local faster-whisper benchmark stays available as prior evidence.
- Use the official Silero 6.2.2 ONNX artifact through ONNX Runtime; the bundled
  faster-whisper VAD missed one recorded phrase in the comparison. The verified
  model uses 512-sample frames, start 0.5, release 0.35, 300 ms silence and
  200 ms pre-roll. Actual endpoint delay is quantized by 32 ms frames. Enforce
  a 15-second utterance limit. Buffer overflow or missing audio is a visible
  fault, never silent data loss. Warm inference before enabling capture.
- The documented ASR API uploads complete audio segments and streams output
  text; it is not continuous input streaming. The first implementation sends
  once after VAD endpointing and displays partial transcript deltas while that
  request runs, followed by one final result. Do not repeatedly retransmit growing
  microphone windows. Generation/utterance identity prevents stale partial or
  final results from triggering a reply after Stop or a newer admitted turn.
- MiniCPM stays at `http://localhost:1234`, model `minicpm5-2b`, reasoning off.
  A changed transcript or completed pause can request advice; never call it
  every audio frame. Limit concurrency to one, coalesce pending updates, and
  label timeout/invalid output as unavailable. Its earlier classification errors
  mean advice is displayed separately from the deterministic turn state.
- A final transcript commits once. In conversation mode, the first test responds
  to completed nonempty utterances; MiniCPM is advisory until its accuracy is
  qualified. Provide an explicit advisory-gating option for comparative tests.
  Partial transcripts never start duplicate remote generations.
- Remote replies use `http://earnests-mac-studio:1234/v1`, explicit model
  `qwen3.8-27b-mlx`, persistent HTTP and streaming visible content. Keep context
  bounded; record model identity and reasoning settings. Reasoning/tool payloads
  are not speech. Limit the first test to short spoken replies. The current
  Qwen model entry lacks a native reasoning switch. A text-only completion
  adapter may render the pinned official template with thinking disabled, as
  verified by the bench; test history formatting and literal control-token
  handling rather than assume chat-template kwargs are honored.
- Reuse the existing Pocket TTS worker and Azelma assets. Admit complete short
  clauses, bound queued text/PCM, and preserve response/clause identities.
  Play through the verified Linux output at the accepted digital cap of 11572
  in signed-16 units. Capture continues during playback. Explicit abort/close
  is required; the diagnostic found that CallbackStop alone did not terminate
  the Pulse duplex stream reliably. Report submitted versus estimated played
  audio separately; do not call an API-return timestamp acoustic latency.
- Each new turn invalidates old generation output before cancelling expensive
  work. Stop has a separate immediate playback path. Disconnects, decode errors
  and HTTP timeouts appear on the dashboard and retire the affected generation.

## Echo and interruption qualification

Linux speaker output is operator-accepted. Echo rejection and double-talk remain
unqualified. The first automatic reply test uses a visible playback guard: input
meter/VAD remain live, but microphone speech candidates during playback and its
short release interval do not automatically trigger new replies. A Stop button
is always active. This is explicitly half-duplex turn admission, not a claim of
working interruption.

An explicit experimental barge-in mode removes that guard and cancels playback
on sustained VAD onset without waiting for MiniCPM or final ASR. Qualify it using
speaker-only playback, near-end speech and double-talk. Expose false interrupts
and missed speech in the evidence. Do not promote it to the default until echo
behavior is measured with this USB firmware, placement and Linux routing.

## Dashboard and ROS observation

Serve a local HTML dashboard on `127.0.0.1:8765`. It shows device/model readiness,
input level/clipping, speech probability, silence countdown, revisable/final
transcripts, MiniCPM advice, actual turn state, streamed remote text, TTS queue,
playback state, cancellation/error events and stage latency. Include Start,
Stop, mode selection and a bounded event timeline. Use text rendering for model
output. A missing/stale connection must be unmistakable.

Use server-sent events for observation plus bounded same-origin POST controls.
The event schema includes schema version, session ID, monotonic timestamp,
sequence, stage, state, utterance/generation ID and bounded stage details. Cap
event history and subscriber queues; a slow browser cannot stall audio.
Measure end-of-speech→final ASR, advice, remote first visible text/first clause,
TTS first PCM, playback submission/start estimate, and interruption request→
software stop. Remote server clocks never enter local duration arithmetic.

A separate, device-free container uses the existing ROS image and a small
observer bridge. It consumes the same event stream and publishes bounded JSON
in `std_msgs/String` on `/alice/conversation/events` in ROS domain 74, with
localhost-only discovery. The dashboard reports the bridge's observed heartbeat;
it must not show ROS connected just because the host service is running.
The bridge has no robot action, motion or hardware write path. Validate messages
using `ros2 topic echo` and a separate subscriber. No browser ROS dependency or
new public WebSocket service is necessary for this bench.

## Delivery and acceptance

1. Inspect the hosted ASR OpenAPI and benchmark the saved synthetic introduction.
   Record endpoint/model identity and measured streaming format. Retain local
   faster-whisper measurements as the comparison baseline.
2. Write failing tests for silence/preroll, utterance bounds, revisions, stale
   results, single turn commit, cancellation priority, queue overflow, streaming
   parsing and event replay/disconnect behavior; then implement the host bench.
3. Exercise the HTML interface with simulated input and injected failures, then
   verify real ROS publication and stale-bridge indication.
4. Run a bounded live microphone session and show the user the dashboard. Verify
   real transcript changes, silence endpointing and the two inference stages.
5. Run short speaker replies, record software latency, and separately conduct
   the echo/barge-in comparison with operator listening feedback. Keep limits
   and failures visible; no servo motion is included.

Store aggregate config/metrics/manifest/conclusion under an experiment directory.
Private audio and any private transcript evidence remain outside the repository.
Document the launch/stop commands and checkpoint the latest measured state.

## Sources

- Existing contracts: `src/alice/speech/tts_worker.py`, ROS `session.py` and
  `audio.py`, and `infra/ros2/compose.audio.yaml`.
- [faster-whisper implementation and CPU usage](https://github.com/SYSTRAN/faster-whisper).
- Pinned English model: `Systran/faster-whisper-base.en`, revision
  `3d3d5dee26484f91867d81cb899cfcf72b96be6c`, MIT model card metadata.
- Current hardware evidence: `hardware/respeaker-usb-audio-evidence-2026-09-21.md`.
