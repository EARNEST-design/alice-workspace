# Audio evidence, wake admission and Kev evaluation

Date: 2026-09-22. Attended ReSpeaker bench, no robot motion or firmware changes.
Implementation and local synthetic qualification; not general speech accuracy.

## Outcome

A candidate wake now passes audio-quality and decision checks before opening the
existing 30-second conversation window. Hosted Qwen remains ASR and remote Qwen
27B remains the answer model and is also the temporary live decision backend. MiniCPM and Kev are explicitly selectable experimental backends. None of the evaluated local Kev candidates qualified as a better live
replacement. Their installations and results are preserved; experimental services
were stopped to release memory. No automatic shadow inference is enabled.

## Evidence and policy

The hosted ASR actually rejects `verbose_json` with HTTP 400, despite the generic
OpenAPI request enum. Its streaming adapter returns no ASR or language confidence;
these are null/unknown. Silero's speech probability is not transcript confidence.
The decision now receives bounded history, the transcript and language tag,
Silero mean probability/voiced fraction/duration, audio level/clipping, and local
speaker-overlap evidence. Qwen and MiniCPM return labels with unknown confidence; Kev
returns decision probabilities, which are not recognition accuracy.

The pinned MIT-licensed pyannote segmentation export from the official sherpa-onnx
release runs on one CPU thread. At >=200 ms detected overlap, <120 ms detected
speech, missing/malformed evidence, or analysis failure, the policy waits. Its
channel labels carry no identity. The synthetic sequential-two-voice case returned
one local channel, so it cannot count people taking turns. Short inputs are padded
but only complete receptive fields inside real audio contribute evidence.

| Synthetic audio | Detected overlap | Max simultaneous | Median analysis |
|---|---:|---:|---:|
| English Azelma | 0.000 s | 1 | 35.6 ms |
| Cantonese female | 0.000 s | 1 | 35.7 ms |
| English/Cantonese mixture | 2.936 s | 2 | 36.0 ms |
| Silence, tone, low white noise | 0.000 s | 0 | 35.9–39.0 ms |

Continuous speech exceeding 15 seconds now emits one discard, releases its
buffer, and waits for 320 ms consecutive quiet. It does not kill the session or
forward a truncated phrase. Stop invalidates pending audio analysis and late
results cannot reopen engagement. Only one native analysis can remain outstanding.

## Kev comparison

Pinned source `90990a5fac2995b9faa3190f7d437e84f2067768`, isolated Python 3.12 and
CPU Torch 2.8.0. The local host has a Ryzen 5 6600H and Radeon 680M integrated GPU.
The small candidates used fp32; the Qwen3 4B candidate used a separately recorded
bf16 LoRA merge followed by FBGEMM dynamic int8 linears and fp32 other parameters.
No numerical parity with upstream fp32 is claimed.

All three received the same frozen 14 synthetic development requests: English and
Cantonese wake calls, questions, follow-ups, other addressees, gibberish, incomplete
phrases and unaddressed background speech. Initial thresholds stayed fixed at
P(SPEAK)>=0.75 and P(coherent)>=0.65.

| Candidate | Matches | Valid calls accepted | Negatives rejected | Median / maximum short latency |
|---|---:|---:|---:|---:|
| Kev 0.6B | 8/14 | 6/6 | 2/8 | 628 / 727 ms |
| Kev 0.8B | 8/14 | 0/6 | 8/8 | 1,181 / 1,510 ms |
| Kev Qwen3 4B int8 | 8/14 | 1/6 | 7/8 | 2,105 / 2,490 ms |

The 4B model accepted an unfinished Cantonese phrase. At the actual application's
maximum character bounds, English requests took 7.45–7.81 seconds and Cantonese
32.40–32.68 seconds. The application's three-second deadline prevents late output,
but disconnecting does not cancel native server work. This is unsuitable for the
requested latency target and would require a different serving/context policy.

The successful 4B loader took 100.3 seconds, with about 5.66 GB final RSS, 11.64 GB
peak RSS and a 12 GiB cgroup cap/no unit swap. Two earlier 10 GiB attempts were
killed by their own cgroup; no existing service was terminated. The final loader
materializes non-linear storage and converts one linear at a time with one thread,
then serves with six. `scripts/serve-kev-int8.py` and the frozen CPU requirements
preserve the tested path. This deployment work did not qualify the model for live
speech admission. The explicit operator preference question about trying Kev
despite these results remains pending. A later complete-evidence test invalidated
the initially proposed MiniCPM fallback; the stated default changed to Qwen.

## Verification and provenance

Before implementation, regression tests reproduced absent evidence, wake bypass,
missing dashboard fields, and unsupported decision arguments. Further review
found mixed model-error/success payloads and malformed speaker evidence; both
now fail closed, with regression coverage. Checkpoint identity is verified via
Kev's `/v1/models` before classification rather than trusting the echoed model
name. Unknown dashboard measurements remain unknown.

Final verification and service observations are recorded below. Evidence is in ignored
`artifacts/conversation/2026-09-22-speech-admission/`: config, raw synthetic probe
requests/results, model/license/environment manifests, comparative negative
results, red/green logs, and review report. No human audio or transcripts are saved.
All existing worktree artifacts and accepted female English/Cantonese voices,
volume/speed, reply guard and Stop behavior are preserved. No commit, merge or push.

## Complete-evidence check and revised default

The richer, frozen 12-case held-out screen supplies the same simulated single-voice
metrics and unknown ASR/language confidence that the real runtime supplies.
MiniCPM returned SPEAK for every case: 6/12 correct, including false accepts for
both languages' gibberish, incomplete phrases and other-person requests. Median
latency was 1.184 seconds, maximum 1.912 seconds. Its earlier bare-evidence result
(12/14, mean 444 ms) was therefore insufficient to qualify real use.

The existing remote Qwen 27B classified all 12 fuller cases correctly, median
938 ms and maximum 1962 ms, with valid exact categorical outputs and successful
finish markers. It returns no decision probability; all confidence fields remain
unknown. Its separate bare-evidence 14-case diagnostic scored 13/14, with one
unaddressed background phrase falsely SPEAK; the deterministic wake gate blocks
that case. No prompt tuning was used to turn the failed full-evidence MiniCPM
screen into a pass. Both failed and successful raw synthetic results are retained.

Qwen admission sends candidate text/history/audio evidence before admission to
the same configured Tailscale host as the reply model. No additional external
provider is introduced. The alias is validated but an immutable model-weight
revision is not exposed by LM Studio. Broader real-room false acceptance/rejection
rates and speaker ownership remain unqualified. Maximum-context probes with
full evidence measured English 748 tokens / 2.919 s and Cantonese 1817 tokens /
5.757 s. The latter exceeds the three-second budget and produces no reply;
computation on the server can continue. The same request was observed to completion
without retries or queued replacement requests.


## Final verification and deployed state

- Full conversation and speech suite: **402 passed in 34.00 s**. Ruff checks and
  formatting passed (27 files); strict mypy passed (14 source files); diff check
  passed. Independent review cleared model protocol and cancellation handling.
- Actual strict Qwen client: an addressed English question returned SPEAK in
  1.358 s; synthetic Cantonese gibberish returned WAIT in 1.051 s. No probability
  or confidence scores were fabricated.
- Only the owned earlier bench process was stopped. Reloaded at
  `http://127.0.0.1:8765` with explicit `--decision-backend qwen`, remote decision
  URL, pinned overlap model and both accepted female voices. ASR, decision and
  reply endpoints ready; existing ROS observer reconnected. Experimental Kev
  unit is inactive; its downloads and isolated environment remain available.
- Actual browser reload confirmed Audio evidence, decision backend, unknown ASR
  confidence, speaker estimate and probability indicators render correctly.
- The deployed synthetic WAV replay completed in 23.09 s including both-voice
  warmup: three ASR finals, three successful overlap analyses (37.2–41.9 ms),
  four dry speech outputs, no errors. English first PCM took 95.9–100.8 ms.
  This was a pipeline replay with the explicit private decision bypass; separate
  strict client and held-out tests qualify admission. No acoustic playback was
  attempted in this replay.
- A subsequent 20.19 s live observation produced 141 capture/VAD meter updates,
  RMS up to 0.0361, all sampled VAD states silence, no ASR requests, no decisions,
  no replies and no errors. This verifies active input and quiet-interval behavior,
  not real human wake recognition or false-acceptance rates. Only redacted stage
  metadata was retained. Wake listening remains available with its five-minute
  session bound; say Alice / 愛麗絲 / 爱丽丝 to attempt a real turn.

Service log (outside repository):
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/bench-service-speech-admission-20260922.log`.
Service execution session: `31582`. Evidence: `tests-final.log`,
`qwen-client-smoke.json`, `reload-replay.json`, `live-redacted.json`, and
`final-manifest.json` under the experiment artifact directory.
