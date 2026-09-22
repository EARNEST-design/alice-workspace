# ADR 0015: Hosted Qwen ASR for the attended conversation bench

- Date: 2026-09-21
- Status: accepted; attended bench implemented and smoke-tested
- Operator instruction: inspect the hosted Qwen ASR docs and integrate that ASR.
- Follow-up: the operator explicitly approved the plan after the bench handoff.

Use the host bench service and HTML dashboard described in
[the conversation design](../superpowers/specs/2026-09-21-live-conversation-bench-design.md),
with hosted Qwen replacing the proposed local faster-whisper recognizer.

The inspected OpenAPI documents `POST /v1/audio/transcriptions` with an audio
file and streaming text response. The live service identifies itself as vLLM
0.14.0, model `qwen3-asr`, root `Qwen/Qwen3-ASR-1.7B`. An actual multipart WAV
request succeeded despite the generated schema labeling its file-bearing form
as URL-encoded. SSE chunks use `choices[].delta.content`, a Qwen language prefix,
`<asr_text>`, a terminal `finish_reason: stop`, then `[DONE]`.

Keep Silero 6.2.2 VAD local. Its completed speech segments are transmitted to the
operator-specified HTTPS service over Tailscale; raw audio is not sent to either
text LLM. The host retains no raw microphone files. Local faster-whisper remains
an evaluated alternative, without an automatic fallback that hides outages.

The API streams transcription output after upload, so the UI must distinguish
capture/endpointing from ASR deltas. No continuous-input WebSocket capability is
assumed. Reject framing errors, overlong output, failed/truncated streams and
stale generations. Preserve TLS verification and request-local cancellation.

Qwen also documents `language None<asr_text>` for no speech. Synthetic silence
and a tone reproduced that response on this endpoint. Accept it as an empty
final transcript only after stop/DONE, and never trigger a reply. Nonempty text
with no-speech metadata remains an error. This does not replace local VAD.

Background-sound follow-up: parse Qwen's canonical language tags independently
of the requested English hint. A live server probe classified reversed
synthetic speech as Arabic despite `language=en`. The former English/None-only
parser incorrectly treated that valid metadata as malformed. In the selected
English mode, consume and validate other-language streams but emit no partial
text, commit an empty ignored result, and make no reply request. The dashboard
shows the detected language and skip reason. Bound the metadata header to 128
characters independently of SSE chunking; still require stop/DONE.

The bench owns Linux audio separately from the reviewed ROS speech runtime.
ROS receives observation events on an isolated domain. No ROS hardware or motor
admission changes are included. The earlier proposed ADR 0014 remains a possible
future migration after this loop is measured.

The first bench keeps MiniCPM advisory, replay dry, and playback guard enabled.
Full duplex, a continuous speaking policy and expanded acoustic/queue metrics
remain deferred. See the [runbook](../conversation-bench.md) and
[experiment evidence](../experiments/2026-09-21-qwen-asr-bench.md).
