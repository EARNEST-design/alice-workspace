# Conversation readiness and weekly status, 2026-09-21

Scope: audit branches and the September 14–20 experiments, check both supplied
LM Studio endpoints, download Qwen3.8-27B to the Mac, and qualify the available
audio test path. Preserve all existing worktrees and local experiment evidence.

## Branch state

Read-only GitHub inspection found no open PRs. Remote heads remain `main`
(`dac579c`) and `feature/phase-2-actuator-identification` (`66d3014`).

| Local branch | Head | Status |
| --- | --- | --- |
| `main` | `714d2fc` | Eight commits ahead of origin/main; existing local checkpoint/architecture notes |
| `codex/robot-description` | `13c2549` | Clean, local; September 15 provisional kinematic visualization |
| `feature/streaming-affect-motion` | `07fc60b` | Local; existing uncommitted hardware/checkpoint/design notes retained |
| `feature/phase-1-passive-blendshapes` | `301e7f2` | Clean; last commit August 31 |
| `feature/phase-2-actuator-identification` | `66d3014` | Clean; matches remote; last commit September 2 |

No merge, push, prune, or branch switch was performed. Fresh speech-unit checks
passed: `tests/speech`, 160 tests in 21.65 seconds. The full ROS/container suite
was not repeated for this diagnostic-only session.

## Last week's results

- September 15: provisional `alice_description` 3D export and browser viewer;
  31 links, 30 joints, 21 independent display coordinates. Geometry, body motor
  assignments and travel remain provisional. The historical report records
  15 host and 15 container checks. Today's host checks passed all 15 tests:
  eight description checks plus seven export checks.
  [3D evidence](../../../robot-description/docs/experiments/2026-09-15-robot-description-validation.md).
- September 15: eight-participant ROS speech runtime and final review complete.
  The recorded final verification was 1,079 passed, three skips covered by host
  checks. Azelma TTS and simulated audio completed with zero underflows. The
  approximately 17 ms admission p99 is an internal control measurement, not
  microphone-to-reply latency. Live ROS hardware still rejects admission pending
  complete ownership visibility. Visible facial movement remains unaccepted.
  [ROS evidence](2026-09-15-ros2-runtime.md).
- September 16: microphone recording and tone playback accepted; peak 11,572
  signed-16-bit was reported clean and loud enough. The full 6.38-second Azelma
  clip played, but listening acceptance is not recorded. The temporary XIAO
  diagnostic used preloaded playback and returned capture data afterward;
  real-time duplex transport, echo-safe interruption and VAD were not qualified.
  [Audio evidence](../../hardware/respeaker-lite-audio-evidence-2026-09-16.md).
- September 16–18: UART trials and software investigation did not recover
  Maestro return bytes. Latest recorded controller profile is fixed UART 9600,
  CRC off, device 12, with no motion and unchanged servo records. This remains
  separate from the audio-only test. [UART evidence](../../hardware/maestro-direct-uart-evidence-2026-09-18.md).
- The Pipecat/ROS conversation design remains proposed and unimplemented.
  [ADR 0014](../architecture/0014-pipecat-conversation-and-ros-speech.md).

## Operator follow-up and STT check

The operator subsequently confirmed that the speakers work correctly as Linux
output. This supersedes the earlier audibility/connector blocker recorded below;
no further speaker troubleshooting is required on that basis. Echo rejection,
double-talk and the end-to-end conversational loop still require qualification.

No matching Whisper/STT process or running Docker container was found locally.
Whisper, faster-whisper, whisperx, sherpa-onnx, Vosk, Pipecat and Wyoming are not
installed in the preserved Alice environment. No recognizer implementation or
STT service configuration was found in source, config, infrastructure or ROS.
Neither LM Studio inventory lists Whisper. This does not exclude an independently
running service on the Mac; its process/service inventory was not inspected.
ADR 0014 contains proposed ASR choices, not a deployed recognizer. The microphone
VAD evidence detects speech activity and silence; it is not transcription.

## Endpoint and decision measurements

Both supplied endpoints returned HTTP 200. MiniCPM5 2B Q4_K_M was available but
unloaded locally; it was loaded at context 4,096 with flash attention requested.
Reported load time was 1.455 seconds. The Mac initially served GPT-OSS 120B and
had Qwen3.6 27B MLX 4-bit downloaded. Tailscale ping used a direct path and
reported approximately 1 ms for three probes.

The first local SSE smoke request returned `SPEAK` after 1,065.8 ms, excluding
model loading. Its first visible text arrived after 972.8 ms. Native API
`reasoning: off`, temperature zero and `store: false` were used.

Eight authored English text contexts, repeated twice over a persistent HTTP
connection, produced 12/16 expected decisions. Both completed questions were
incorrectly classified `WAIT` on both repetitions. The second pass's decision
completion was 240.8–361.4 ms, median 245.1 ms. An exploratory prompt clarification
on the same eight cases produced 6/8 expected decisions and incorrectly treated
the supplied speaker-echo context as `SPEAK`.

These are development smoke tests on text descriptions, not a held-out
conversation evaluation, microphone recognition test, measured echo detector,
or proof of reliability. No training occurred; no microphone data was sent to
either endpoint. The native API did not expose an explicit seed in these calls.

Qwen3.8-27B MLX 4-bit download was requested through the Mac's native
`POST /api/v1/models/download`, using the exact LM Studio community repository:
`https://huggingface.co/lmstudio-community/Qwen3.8-27B-MLX-4bit`.
Job: `job_bd27f04e26`; expected size: 16,081,498,220 bytes.
Refer to the captured download status for completion; starting a job is not
evidence of successful model loading or inference.

## Audio preflight and proposed next test

Initially only XIAO `303a:1001`, serial `E0:72:A1:FB:E5:DC`, was connected.
After the user selected direct XMOS USB, ReSpeaker Lite `2886:0019`, serial
`0000000001`, enumerated at USB path `6-1`. Its 1.08 descriptor and three DFU
alternate settings exposed no Audio-class interface or ALSA input/output.
This matches the recorded I2S 1.0.8 profile.

The official USB 2.0.7 firmware and official I2S 1.0.8 restore image were staged
outside the repository. Both downloaded blobs matched the official Git tree at
`2ad81e22e773d1680793acdb3e14bb108d7566cb`. Firmware readback returned zero bytes
and printed `Failed` despite exit code zero. It is not a backup.

The user then explicitly approved switching and testing USB audio. The upgrade
partition received all 278,528 bytes. dfu-util returned 251 at reset; independent
re-enumeration then verified version 2.07, ALSA card `Lite`, and 16 kHz stereo
S16_LE playback and capture interfaces. Factory and data partitions were not
targeted. USB firmware remains installed for the continued test.

The initial 30-second PortAudio duplex probe captured all 480,000 frames, with
no callback status errors or clipped input samples. Playback was limited to peak
2,048. The user reported no audible introduction and later clarified they had
not spoken. VAD found no speech. This cannot qualify echo rejection or speaker
audibility. PortAudio kept issuing callbacks after CallbackStop; the 35-second
wall deadline aborted it. A separate two-second silent diagnostic explicitly
aborted after the sample bound, with no status errors and `active=false` afterward.
Its 0.026 ms abort call is a host API observation, not acoustic stop latency.

A native PipeWire replay used the saved seven-second clip at its prior accepted
digital peak 11,572. The current device profile was Digital Stereo (IEC958) plus
Analog Stereo input, unmuted, with output channel volume 0.288499 (UI about 66%).
This profile differed from the initial analog profile; this session did not
change the device profile or its volume. The active graph verified pw-play ->
ReSpeaker output and ReSpeaker input -> pw-record. Playback exited zero after
7.044 seconds; the eleven-second capture had one saturated sample per channel.
The user reported still silent, then said they had heard it at some point.
Consistent audibility remains unaccepted. The source/capture whole-clip normalized
correlation was 0.256; this does not identify an acoustic route or prove AEC.

A subsequent twenty-second microphone-only capture contained exactly 320,000
frames and no clipped samples. Post-capture Silero analysis found two speech
bursts (start observations 4.736 s and 7.200 s). With thresholds 0.5/0.35 and
300 ms silence, end decisions occurred 320 ms after the first below-release
frame, at 5.664 s and 7.936 s. The 32 ms framing quantizes the timeout. These
are analysis results on the captured microphone signal, not a deployed turn
manager, an ASR transcript, or a calibrated human-endpoint latency result.

Both native recordings reached their exact requested frame count but exited 1.
Inspection of upstream PipeWire 1.6.2 `src/tools/pw-cat.c` found that sample-limit
completion quits the loop without setting `data.drained`, while main sets success
only when drained (lines 1042–1046 and 2654–2659). Keep the raw exits; validate
the actual WAV frames rather than treating exit 1 alone as lost recording data.
No PipeWire source was changed.

Silero 6.2.2's wheel and TorchScript asset were hashed and staged privately;
no repository dependency was installed. Offline synthetic Azelma plus digital
silence took median 0.269 ms per 512-sample frame, p95 0.316 ms, one CPU thread.
Digital silence probability stayed below 0.009; generated speech was detected.
The concurrent duplex test had approximately 1.09 ms median inference time.
Neither result validates room noise, semantic turn completion, or double-talk.

The existing GPT-OSS 120B remote endpoint was also exercised once with SSE:
first visible text 1,698 ms, complete short greeting 1,823 ms, reasoning low.
These figures are for GPT-OSS, not the still-downloading Qwen model.

Recommended conversation boundary: continuous local VAD and bounded pre-roll,
streaming ASR, then an optional contextual MiniCPM adviser on changed transcripts
and pause events. A completed turn starts remote Qwen streaming; complete clauses
feed persistent TTS. Validate new human speech locally and cancel/flush obsolete
audio without waiting for either LLM. Prevent stale replies from restarting audio.
MiniCPM5 2B is a text model; a separate recognizer is still required.

Start silence-threshold experiments around 200–350 ms, measuring truncation and
false triggers rather than assuming that duration means a finished turn. Qualify
quiet room, background conversation, near-end speech, speaker-only echo and
simultaneous speech separately. Measure end-of-speech to first audible reply,
first usable Qwen clause, TTS first PCM, stream underflows and interruption to
silence independently. Existing 200 ms playback buffering and 100 ms mouth
lookahead must be accounted for when reconnecting speech to facial motion.

## Evidence

Aggregate requests, responses and measurements:
`artifacts/conversation/2026-09-21-readiness/` in this worktree.
Private hardware staging:
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/`.

Official references:
[LM Studio download API](https://lmstudio.ai/docs/developer/rest/download),
[Qwen model catalog](https://lmstudio.ai/models/qwen/qwen3.8-27b),
[ReSpeaker firmware modes](https://github.com/respeaker/ReSpeaker_Lite/),
[Silero VAD](https://github.com/snakers4/silero-vad).
