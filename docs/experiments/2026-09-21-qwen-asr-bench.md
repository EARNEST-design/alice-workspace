# Hosted Qwen ASR conversation bench

Date: 2026-09-21. Operator selected English first, then explicitly requested
integration with the hosted Qwen ASR service. This supersedes the proposed local
faster-whisper recognizer for the first live bench; its earlier measurements and
private environment are preserved.

## Integration

The service is implemented in `alice.conversation`. A local HTML dashboard shows
capture level, VAD probability and endpointing, provisional/final ASR text,
MiniCPM advice, streamed Qwen reply, Azelma synthesis, output and ROS state.
Start/Stop own a bounded microphone session. Synthetic replay uses the real
inference endpoints with dry output. A device-free ROS observer publishes
versioned JSON events on domain 74, with local-only discovery.

The [inspected ASR documentation](https://work.manakin-gecko.ts.net:10000/docs)
advertises `/v1/audio/transcriptions`. Actual multipart WAV requests work;
the generated schema's URL-encoded form label is misleading for the file field.
The endpoint reports vLLM 0.14.0 and model `qwen3-asr`, root
`Qwen/Qwen3-ASR-1.7B`. A checkpoint revision was not advertised. Requests use
16 kHz mono PCM16, English, temperature 0, seed 7 and `stream=true` with TLS
verification. The adapter removes the fragmented `language English<asr_text>`
prefix, bounds records/output and requires both successful stop and DONE before
committing a final transcript. Streaming occurs after segment upload.

Silero 6.2.2 ONNX remains local. Its verified artifact SHA256 is
`1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
The bench uses 512-sample frames, 0.5/0.35 start/release thresholds, 200 ms
pre-roll, 300 ms silence and a 15-second utterance limit. Digital silence is
rejected before ASR admission. MiniCPM's SPEAK/WAIT result remains advisory
because earlier classification probes contained errors. Remote Qwen3.8-27B MLX
uses its previously tested text-only non-thinking completion template. Azelma
uses the existing offline PocketTtsWorker, seed 7, with the accepted digital
peak limit 11572/32768 and native PipeWire output.

## Evidence and measurement conventions

The first actual ASR probe used a previously generated seven-second Azelma
introduction. First transcript text arrived at **113.61 ms**, and the validated
stream completed at **174.19 ms** from request start. This is one warm synthetic
smoke trial, not a held-out accuracy score or latency percentile. Language-prefix
tokens are excluded from time to first text.

An initial full synthetic replay reached hosted ASR, local MiniCPM, remote Qwen
and dry Azelma output. Capture was inactive. Later verification results are
recorded in the aggregate metrics and logs below. ASR latency starts at upload
preparation after endpoint/cancellation work; it does not include waiting for
end-of-speech. Native output submission/drain and Stop timings are software
observations; acoustic start/stop have not been measured.

The independent code review reproduced a pending Start resuming after Stop,
an oversized ROS reset rejection and cancelled stages remaining active. It also
identified missing capture lag/overflow detection. Focused regression tests and
fixes address lifecycle intent ownership, bounded cancellation, timestamped
capture buffering, explicit retirement states and bounded full-history ROS
reset parsing. Browser reconnect/freshness findings are tracked in the same
review artifacts. Live route inspection also found `pactl` absent; the host's
native PipeWire JSON inventory is the supported discovery path.

A real observer stop/restart produced connected → disconnected → connected in
the dashboard. An independent ROS subscriber received two ordered
`bench-event/v1` events; their host-monotonic ages on receipt were 1.41 and
1.18 ms. These two local heartbeat observations are not general ROS latency
statistics. All 160 existing speech tests passed after dependency integration.

The physical-output smoke used the same synthetic seven-second input, then
actual ASR → MiniCPM → Qwen reply → Azelma → native ReSpeaker playback. ASR
final took **182.18 ms**, MiniCPM **281.16 ms**, and the reply completed in
**1014.42 ms**. The player drained **74,880 samples at 24 kHz** (3.12 seconds).
Total including cold TTS warmup was 7.56 seconds. The native route was verified;
this run did not obtain a separate human audibility confirmation.

The first 20-second live capture admitted one 2.696-second speech-like segment
after 320 ms silence. ASR rejected its metadata prefix; no transcript or reply
was committed. The actual prefix and raw audio were not retained, so the cause
of that individual segment cannot be reconstructed. Subsequent synthetic
silence and tone probes both returned `language None<asr_text>`, a valid
[Qwen no-speech format](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/utils.py)
missing from the initial parser. The adapter now accepts it as an empty final
after stop/DONE, rejects nonempty no-speech content, and produces no model or
speaker request. A real adapter check on five seconds of digital silence
returned exactly one empty final in 86.12 ms.

The second 20-second live window had **158 capture-meter events**, peak
**0.02466**, maximum VAD probability **0.06514**, no endpoints, no ASR calls,
no replies and no faults. Stop returned in **6.90 ms**. The microphone is now
stopped; the service and ROS observer remain available. No live human phrase
was successfully transcribed in these two windows; live conversation accuracy
and room echo still need an attended spoken test. These two windows establish
capture/stop operation and quiet-room rejection, not a completed human dialogue.

Final verification: **92 conversation tests**, **160 existing speech tests**,
Ruff lint/format, strict mypy on all ten conversation modules, lock consistency
and diff whitespace checks pass. A real browser page adopted a new server
session whose numeric cursor exceeded its old one without reloading; its old
timeline was replaced. The initial review and focused follow-up preserve the
actual issues, regressions and remaining qualifications.

## Provenance, artifacts and limits

Local ignored evidence is under
`artifacts/conversation/2026-09-21-qwen-asr/`: config, inspected OpenAPI/model/
version responses, synthetic probe, aggregate metrics, tests, review reports,
ROS evidence, artifact hashes and conclusion. The fixture is previously
generated Azelma speech, seed 7; its source hash is in `config.json`. No new raw
microphone audio or operator transcripts are retained in the repository. The
in-memory dashboard and ROS event history can contain transcripts. Remote
endpoint retention was not inspected.

This is an attended bench integration, separate from production ROS speech
admission. Echo cancellation, full-duplex interruption and a continuously
context-aware speaking policy remain unqualified. The initial implementation
defers MiniCPM veto comparisons, audible dashboard replay, explicit TTS queue/
clause metrics and acoustic played-sample estimates. The earlier local
faster-whisper experiment remains an alternative; failures do not silently
switch recognizers.

Launch and operator controls are in [the runbook](../conversation-bench.md).
The architecture decision is [ADR 0015](../architecture/0015-qwen-asr-conversation-bench.md).
No motor, firmware, merge or push action is part of this integration.
