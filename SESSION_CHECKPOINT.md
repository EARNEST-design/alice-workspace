# Alice session checkpoint

## Next-session handoff and GitHub sync, 2026-09-22

User requested syncing all current work to GitHub and planning the next phase.
Start with [the handoff](https://github.com/EARNEST-design/alice-workspace/blob/feature/streaming-affect-motion/docs/checkpoints/2026-09-22-conversation-face-handoff.md)
on `feature/streaming-affect-motion`, preserving the existing worktree at
`/home/alice/alice-workspace/.worktrees/streaming-affect-motion`.
ADR 0020 and two September 22 plans cover conversation-to-ROS speech/face
integration and dedicated small admission evaluation. No implementation or servo
motion was requested for this handoff session.

User preference is now explicit: Kev or original TypeSafe Jev should handle
speaking decisions after qualification, with Qwen reserved for answers. Earlier
unanswered preference notes below are superseded. Qwen admission remains the
working temporary baseline (8 s maximum); no backend was switched in this task.
Original Jev is a documented hosted API, not the Kev `jev-latest` alias.

Reuse existing DAC clock, 100 ms mouth lead, selected-face guards and ROS
RunSpeech authority. Current pw-cat conversation playback still has no servo
link. Authored delivery cues and small-classifier evaluation are separate tracks;
physical expression acceptance and Diart speaker coverage remain incomplete.

Fresh validation: 1,128 host tests; 199 ROS/description tests plus two wheel tests
after restoring the required cache mount; host packaging covers its Docker skip.
Ruff/format/strict mypy (101 files)/lock checks pass. See
`docs/experiments/2026-09-22-github-handoff.md` for setup failures and full accounting.
Publish code/tests/reviewed docs across all five branches without merging them.
Ignored model files, environments and private experiment artifacts stay local.
This section supersedes old no-push statements about this synchronization task;
the dated experiment sections remain historical evidence.

## Qwen admission timeout resilience, 2026-09-22

A real wake was recognized and passed audio quality but its remote Qwen decision
exceeded the old 3 s deadline; no reply/TTS was started. Subsequent same-request
probes completed SPEAK in 1.1–1.4 s, and the operator confirmed an audible answer
before this repair was deployed. Original server/network slowdown cause unproven.
Only Qwen admission budget increased to 8 s total/request (connect still 3 s);
fast results immediate, errors still wait, Stop/generation/wake expiry unchanged.
Local Kev/MiniCPM stay at 3 s. No model/voice/prompt/quality/hardware change.

90 model/runtime tests passed (delayed replies red then green; stalled cancellation),
Ruff/format/strict mypy/diff checks clean; independent review approved. Frozen long
synthetic contexts still WAIT, short greeting SPEAK; cache timings are not a speedup.
Latest owned service session 48265; log
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/bench-service-decision-timeout-20260922.log`.
Reloaded with same accepted voice and remote-Qwen args, wake listening, five-minute
bound, both voices warm, output enabled, ROS connected. Kev/Diart not active; MiniCPM
unloaded. Details ADR 0018 amendment, docs/experiments/2026-09-22-decision-timeout.md
and ignored artifacts/conversation/2026-09-22-decision-timeout/. No raw human data,
motion, firmware, commit/merge/push. Preserve all prior worktree/artifacts.

## Idle memory relief, 2026-09-22

Operator reported pressure; Linux host measured ~16 GiB available with zero memory
PSI averages. Historical OOM events were Kev service cgroup limits; Kev/Diart stopped.
Unloaded unused local MiniCPM via LM Studio CLI (API confirms no loaded local models).
Restarted exact owned idle conversation service, reaping idle CosyVoice 4.79 GiB RSS
and PocketTTS 0.88 GiB RSS. Available RAM rose 5.41 GiB to 22.17 GiB after this restart.
Old swap remains; no current sustained memory pressure observed. No code change.

Latest service execution session 38809, log
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/bench-service-memory-relief-20260922.log`.
Same accepted launch settings, dashboard idle, live output enabled, ASR EN/Yue and
remote Qwen decision/reply ready, ROS connected; no voice workers until next Start.
Both voices warm before capture. Stop still retains idle voice workers; restart
was a one-time release, not automatic idle unloading. No live audio test performed.

Keep small audio analysis local. Cantonese TTS is the largest offload candidate if
needed; no remote TTS deployment performed. Mac advertises Qwen plus GPT-OSS 120B
loaded; do not unload unrelated remote models speculatively. Warning-host question
pending, so no claim that another host's pressure was resolved. Redacted evidence:
`docs/experiments/2026-09-22-memory-relief.md` and ignored corresponding artifacts.
Preserve worktree, model files, all prior evidence. No hardware/firmware/motion,
commit, merge or push. This supersedes the older service execution session only.

## Local Diart trial completed, 2026-09-22

User approved trying Diart, prefers local audio compute, remote compute available
if necessary. Installed isolated Diart at
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/diart/env`
(Python 3.12/Torch 2.8 CPU, pinned source 392d53a1b0cd67701ecc20b683bb10614df2f7fc).
Existing local segmentation ONNX plus public Apache-2.0 WeSpeaker ONNX; all
segmentation/embedding/clustering ran locally. Existing hosted Qwen ASR remains
remote and unchanged. No live service/decision prompt/voice/hardware change.

Actual synthetic trial: five-second causal windows, half-second update, one
Torch/ONNX compute thread, about 0.6 GiB process RSS, usual speech step 90–165 ms,
maximum 210 ms corrected initial screen. Local compute is sufficient for this workload;
no remote diarization needed. Buffering delay is additional to compute. Fourteen
synthetic fixtures total 182.91 seconds; original development/held-out phrases
separate, plus fresh confirmation. Count/returning-category relations correct on
raw dominant labels, but initial/new-turn coverage does not consistently satisfy
frozen 70% coverage / 80% purity criteria. Default 0.5 s delay: 4/7 development and 0/4
original held-out gates. Fixed 1.0 s delay: 6/7 development and 1/2 fresh confirmation;
failed fresh first B turn 61.25% coverage, later B 98.42%, pure categories throughout.
Do not present raw category consistency as full qualification or reliable person ID.

Upstream startup prefix would re-emit old 0–5 s labels when padded stream reached 0;
fixed collector publication bounds with regression, superseded outputs retained.
Independent review verifies powerset mapping, batching, threads, causality. Five adapter
tests and full 407 conversation/speech tests passed (34.26 s), static checks clean.
Trial runner: scripts/diart_trial.py; frozen requirements/diart-cpu-py312.txt.

Bounded 20 s microphone-only timing probe: 40 half-second frames, 37.0/38.6/39.5 ms
median/p95/max compute, 23.8 ms capture completion drift, low input RMS max 0.00286,
zero speaker labels, no error. Owned capture stopped. No live human recognition
qualification, no raw mic/transcript/embedding/person tracks saved. Synthetic WAVs
remain private outside repo, preset voice/model/seed hashes in manifests.

Eight paired oracle context cases (no audio) confirmed context alone does not fix admission:
Qwen 7/8 with full context (one peer-answer false SPEAK), MiniCPM 5/8 (all SPEAK). Two baseline
cases underidentified; no overall accuracy gain claim. Context adds both heard text and
labels; not an isolated speaker-label ablation or measured Diart-to-decision test.

Diart is an installed runnable trial, not activated in Alice's live reply loop.
Proposed next stage is bounded local shadow worker with short heard-turn context and
unknown/stale/mixed-speaker handling; require independent room/Cantonese tests.
Latest accepted live policy remains ADR 0018; trial decision ADR 0019 and report
`docs/experiments/2026-09-22-diart-local.md`. Ignored artifacts under
`artifacts/conversation/2026-09-22-diart/` include all configs/negative and positive
results, model licenses/hashes, redacted live timing, final manifest and timeline PNG.
Preserve worktree and all previous artifacts. No motion, firmware, commit, merge or push.

## Audio evidence and decision-backend evaluation, 2026-09-22

Latest live service uses existing remote Qwen `qwen3.8-27b-mlx` for admission
and replies, hosted Qwen ASR, pinned local pyannote ONNX overlap detection and
the accepted female English/Cantonese voices. Dashboard reloaded at
http://127.0.0.1:8765. The owned service was restarted with explicit Qwen URL and
`--overlap-model`; execution session 31582; log:
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/bench-service-speech-admission-20260922.log`.

User requested Kev to replace MiniCPM plus confidence/multiple-speaker evidence.
Installed/pinned isolated Kev and tested 0.6B, 0.8B and Qwen3 4B int8. None qualified:
all scored 8/14 synthetic development cases; 4B rejected five of six valid calls
and accepted an unfinished Cantonese fragment. Maximum-size Cantonese requests
took 32.4–32.7 s. Kev services are stopped, files/env/loader remain for experiments.
Do not present Kev as active or qualified. MiniCPM initially looked usable on bare
text (12/14), but failed the frozen full-audio-evidence screen (6/12, all SPEAK).
Its initial fallback recommendation was withdrawn. Remote Qwen passed that same
12-case screen, median 938 ms / maximum 1962 ms. It is the stated temporary live
default; local backends remain selectable experiments. Optional preference reply
about Kev remains unanswered; no approval is inferred from elapsed time.

ASR rejects verbose_json and exposes no recognition/language confidence: retain
unknown/null, never substitute VAD or decision scores. Qwen admission is categorical
SPEAK/WAIT with strict framing/model checks and a three-second total deadline.
The longest synthetic Cantonese context took 5.757 s, so such a turn waits without
reply; remote computation may continue. Candidate text/history/audio evidence now
goes before admission to the same configured Tailscale host used for replies.
Alias validated, immutable server weight revision unavailable.

All real wake candidates now pass audio and decision checks before opening the
30-second window. Outside window without wake name, no decision/reply/TTS call.
Wait at >=200 ms detected overlap, <120 ms speech, or analysis failure; unknown
fields shown in dashboard. One tracked native analysis at a time; Stop/generation
checks reject late results. Overlap detects simultaneous speech, not speaker
identity or reliable count of people taking turns. Default waits on overlap and
allows sequential speakers; optional preference question remains unanswered.
Continuous >15 s VAD now discards once and rearms after 320 ms consecutive quiet,
rather than stopping the session or sending truncated garbage to ASR.

Final 402 conversation/speech tests passed in 34.00 s; Ruff/format/strict mypy/diff
checks clean; independent review cleared. Real Qwen client accepted a clear
English question and rejected Cantonese gibberish. Reloaded synthetic dry pipeline
passed three ASR/analysis turns and four dry TTS outputs, no errors (23.09 s with
warmup; overlap 37.2–41.9 ms). Replay explicitly bypasses wake/decision and is not
admission accuracy evidence. Subsequent 20.19 s live observation: 141 meter samples,
RMS max 0.0361, sampled VAD silence, no ASR/decision/reply/error. Left wake listening
with the five-minute session bound; no human wake/audio-output success claimed.
Existing reply guard, accepted voice volume/speed and Stop remain unchanged.

ADR 0018, docs/experiments/2026-09-22-speech-admission-kev.md and ignored
artifacts/conversation/2026-09-22-speech-admission/ hold policy, pinned provenance,
negative and positive synthetic results, final manifest and redacted live evidence.
Preserve existing worktree/artifacts. No raw human audio/transcript saved, no
motion/firmware change, no commit/merge/push. This section supersedes earlier
MiniCPM-default and wake-bypass descriptions below.

## Language-switch interruption fix and live recovery, 2026-09-22

Operator confirmed ReSpeaker had been muted and unmuted it; the earlier zero
input issue is resolved. The subsequent reported language-switch freeze was
an actual microphone capture queue overflow: new speech cancelled generating
Cantonese TTS; waiting 359 ms for worker shutdown blocked capture beyond its
four-frame buffer. Default guard covered playback but not pending generation.

Fixed default mode to keep draining/metering audio while deferring new speech
admission throughout the pending turn. Endpoint resets under reply_guard; Stop
remains immediate/bounded and later language turns work. Both live modes now
warm both voices before microphone capture (Listen had incorrectly skipped it).
No queue enlargement, threshold/gain/voice/firmware/wake-name change. Experimental
barge-in remains opt-in/unqualified; its interrupt path is outside this repair.

Three actual synthetic phrases English→Cantonese→English all drained to the
verified ReSpeaker route. First PCM 105/4671/119 ms; Cantonese 3.04 seconds of
audio. Audibility question is pending; do not equate native drain with hearing.
45 runtime tests and full 326 conversation/speech tests pass, static checks
clean, independent review has no important findings. Red tests reproduced both
capture overflows and missing Listen warmup before the fix.

Corrected service reloaded at http://127.0.0.1:8765 and Wake listening started.
Post-reload 15-second live observation: 119 meter samples, RMS up to 0.0875,
speech probability up to 98.6%, four ASR finals, zero errors. No wake/reply in
that observation; it remains listening/awaiting wake with five-minute session
limit and ROS connected. Log:
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/bench-service-reply-guard-20260922.log`.
Evidence: docs/experiments/2026-09-22-language-switch.md and ignored
artifacts/conversation/2026-09-22-language-switch/ in the preserved worktree.
Only redacted stage metadata/synthetic text retained; no human transcript/audio,
motion, firmware, commit/merge/push action. Historical pending mute note below
is superseded by this operator confirmation and measured live recovery.

## Zero-input investigation pending operator check, 2026-09-22

Speech-probability meter is live but incoming ReSpeaker PCM is exact zero.
Independent 3-second stereo capture: 48,000 zeros per channel; software source
and links running/unmuted at unity volume; USB firmware remains 2.07. Known
synthetic input scores >99.99% in the pinned Silero VAD. No capture saved, no
code/settings/firmware changes. Physical mute is suspected, unconfirmed; asked
operator whether board red mute LED is lit and to unmute only if lit. Existing
bounded live session left available; it still expires at five minutes. Do not
claim fixed or lower VAD threshold. Evidence in preserved worktree:
`artifacts/conversation/2026-09-22-zero-input/` and hardware USB evidence note.

## Female Cantonese voice and wake gate, 2026-09-21

Current bench is reloaded at http://127.0.0.1:8765 with live audio enabled,
English+Cantonese ASR, English Azelma and local CosyVoice-300M-SFT preset 粤语女.
The operator accepted the longer female sample's voice, pace and volume.
Cantonese speed is 0.85, normalized speech level under the existing digital cap.
Source/model/env are pinned; see ADR 0016 and docs/conversation-bench.md.
The earlier Nano Cantonese voice was rejected (quiet, male-sounding); do not
relaunch with its old --cantonese-model path. Current launch requires all three
CosyVoice model/source/python flags documented in the runbook. Service log:
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/bench-service-female-wake.log`.

Both Wake listening and Conversation require Alice / 愛麗絲 / 爱丽丝 at the
start of a final transcript. Chinese names can directly precede the question.
Follow-ups are eligible for 30 seconds after successful output; MiniCPM now
actually vetoes WAIT/errors. Outside the window there is no text-model/TTS
request. Stop/expiry clear engagement; deadline is rechecked after the decision.
ADR 0017 records policy. Final MiniCPM prompt sanity probes passed 5/5, but four
repeat examples from the prompt and are not held-out accuracy evidence.

322 conversation/speech tests passed, followed by 46 model/runtime tests after
the final prompt-only adjustment. Ruff/format/strict mypy, lock check and diff
checks pass. Independent reviews cleared wake and process cancellation fixes.
Actual longer female playback drained 6.15 seconds and was accepted. Actual
warm Cantonese synthetic-ASR→remote-Qwen→dry-female-output passed (10.06 s turn,
8.86 s TTS for 4.24 s audio; both-voice warmup 16.30 s). Female CPU latency still
needs improvement. Reloaded service real synthetic English WAV→VAD→hosted-ASR→
remote-Qwen→Azelma dry replay passed; microphone stayed inactive. Final Stop
left the session idle/capture stopped. Existing ROS observer is connected.

Hosted speech ASR regressions pass. A later direct digital-silence probe returned
server finish_reason=error rather than valid no-speech metadata; client fails
closed. Local VAD remains the silence gate. No broad pronunciation, contextual
classification or full-duplex qualification is claimed. No new retained human
mic/transcript artifacts, motor or firmware changes, commit/merge/push occurred.
Evidence: docs/experiments/2026-09-21-cantonese-conversation.md and ignored
artifacts/conversation/2026-09-21-cantonese/ and 2026-09-21-wake-gate/ in the
preserved `.worktrees/streaming-affect-motion` worktree. Preserve earlier
artifacts; historical states below are superseded by this section.

## Qwen metadata background-sound fix, 2026-09-21

Operator reported prefix errors during background sound. A live ASR probe on
reversed synthetic speech reproduced a valid Arabic tag despite language=en;
the old English/None-only parser rejected it. Parser now accepts canonical Qwen
language metadata and whitespace/chunk variation, with a 128-character header
bound. In English mode, other languages produce one empty ignored final after
stop/DONE, zero partial text, no model/speaker request, and an explanatory ASR
card. English and None behavior remain intact. No retained human audio used.

102 conversation tests, Ruff and strict mypy pass; scoped review has no
unresolved findings. Actual endpoint checks pass for garbled Arabic-tagged
synthetic audio, English speech and digital silence. Service reloaded and idle
at http://127.0.0.1:8765, ROS observer retained. The specific operator event's
language remains unknown; raw data was not retained. Background English
hallucination/echo qualification remains separate from this metadata fix.

Details: `docs/experiments/2026-09-21-qwen-asr-metadata.md` and ignored
`artifacts/conversation/2026-09-21-qwen-asr-metadata/` in the existing worktree.

## Hosted Qwen ASR bench, 2026-09-21

The operator explicitly requested hosted ASR at
`https://work.manakin-gecko.ts.net:10000/docs`; this accepts the earlier bench
direction with Qwen replacing faster-whisper. Implementation is in the preserved
`.worktrees/streaming-affect-motion` worktree, `src/alice/conversation/`.

The host service is running idle at http://127.0.0.1:8765 with live speaker output
enabled. The microphone is stopped. Choose Conversation and Start for a bounded
five-minute session; Stop cancels the turn and audio. A device-free container
`alice-conversation-observer` publishes `/alice/conversation/events` on ROS
domain 74 with local-only discovery. Runbook: `docs/conversation-bench.md`.
Do not create duplicate servers/observers; preserve current worktree/artifacts.

Qwen ASR reports model `qwen3-asr`, root `Qwen/Qwen3-ASR-1.7B`, vLLM 0.14.0.
Multipart WAV upload followed by SSE works with TLS verification. Local Silero
6.2.2 VAD remains the audio gate (English, 300 ms silence, 200 ms pre-roll).
The adapter handles Qwen's valid no-speech metadata as an empty final and never
replies to it. MiniCPM remains advisory; remote `qwen3.8-27b-mlx` is downloaded
and loaded, using the inspected non-thinking completion template. Azelma uses
the existing offline worker and accepted digital output cap.

Synthetic ASR-to-physical-speaker smoke passed: ASR final 182 ms, MiniCPM
281 ms, reply complete 1014 ms, native playback drained 3.12 seconds. The
first live window detected one speech-like segment (endpoint 320 ms) but ASR
rejected its unknown metadata; no raw/prefix was retained. Synthetic probes
then exposed and fixed the valid no-speech prefix case. The second 20-second
live window had no VAD-positive speech, endpoints, ASR calls or faults; Stop
returned in 6.90 ms. A human phrase has not been successfully transcribed in
these windows. Echo/full-duplex interruption and a continuous speaking policy
remain unqualified; default playback guard stays enabled.

Final verification: 92 conversation tests and 160 existing speech regressions
pass, with Ruff, strict mypy and lock consistency. Independent review and
regressions address lifecycle ownership, late cleanup, bounded capture backlog,
terminal stage states and browser/ROS session reconnect. No raw audio/operator
transcripts were saved in the repo, and no motor/firmware/merge/push action was
performed. Current ReSpeaker input and output both resolve to `.iec958-stereo`
via native `pw-dump`; `pactl` is absent. USB 2.0.7 remains unchanged.

Evidence: `docs/experiments/2026-09-21-qwen-asr-bench.md`, ADR 0015 and ignored
`artifacts/conversation/2026-09-21-qwen-asr/`. Earlier faster-whisper feasibility
and hardware notes below are historical; preserve them.

## Local recognizer feasibility follow-up, 2026-09-21

English first is operator-selected. Faster-whisper 1.2.1 / CTranslate2 4.8.2
base.en CPU INT8, four threads, was installed in a private isolated environment
and tested alongside resident local MiniCPM. Seven-second replay decode median
489 ms during overlapping MiniCPM requests; decisions median 225 ms (n=3).
Official Silero 6.2.2 ONNX detected both earlier operator phrases and no digital
silence; ungated Whisper hallucinated on silence and its bundled VAD missed
one phrase. Use explicit VAD admission and the verified artifact.

Qwen3.8-27B MLX 4-bit download completed and the model is loaded on the Mac
with 8192 context. This raw model entry has no native reasoning-off setting;
a per-request, manually rendered non-thinking /v1/completions probe returned
first visible text in about 461 ms warm. Global model settings were unchanged.
No persistent ASR service/dashboard/ROS telemetry is implemented yet. The
operator has a pending design approval for the host bench plus ROS observation.
No new recording, playback or motion occurred in this follow-up.

Evidence: `docs/experiments/2026-09-21-local-faster-whisper.md` and
`docs/superpowers/specs/2026-09-21-live-conversation-bench-design.md` in the
streaming-affect-motion worktree. Preserve the existing worktree and artifacts.
Private interpreter:
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/faster-whisper-env/bin/python`.

## Direct USB audio and conversation readiness, 2026-09-21

**Current XMOS firmware is USB 2.0.7**, explicitly approved by the operator.
The cable is on the XMOS port, ReSpeaker `2886:0019`, serial `0000000001`, USB
path `6-1`; ALSA `Lite` supports 16 kHz stereo S16_LE input/output. The earlier
XIAO I2S path needs I2S firmware restored before reuse. Official I2S 1.0.8 restore
image is saved privately; device readback was empty, so no exact backup is claimed.

A 20-second microphone capture has two detected speech bursts and no clipping.
Post-capture Silero analysis ends each burst after 320 ms under a 300 ms silence
setting and 32 ms frames. The operator subsequently confirmed that the speakers
work correctly as Linux output; that resolves the earlier output-path question.
Echo rejection and the complete conversation loop remain unqualified. No local
Whisper/STT service or installed recognizer was found on follow-up; neither LM
Studio endpoint lists Whisper. Separate Mac STT processes remain uninspected.
No permanent ASR/VAD/conversation node exists. Existing PortAudio CallbackStop did not finish the first diagnostic;
bounded explicit abort passed a separate silent test. Native PipeWire playback
completed with verified routing. All test-owned streams have ended.

MiniCPM5 2B is loaded locally, 4,096 context, reasoning off in the probes. Warm
synthetic decisions took 241–361 ms, with classification errors; keep it advisory.
Remote LM Studio was reachable over direct Tailscale. Qwen3.8-27B MLX 4-bit
is downloading on the Mac, job `job_bd27f04e26`; inspect API status before claiming
completion/loading. Existing GPT-OSS endpoint streamed a reply. Fresh checks:
160 speech tests and all 15 current 3D/export tests passed. No merge/push/branch changes.

Evidence and continuation:
`/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/experiments/2026-09-21-conversation-readiness.md`
and `hardware/respeaker-usb-audio-evidence-2026-09-21.md` in that worktree.
Raw captures/firmware stay under
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/`.
Preserve all earlier worktree notes, accepted limits and experiment artifacts.

## ReSpeaker continuation, 2026-09-16

**Microphone recording and bounded tone playback are now operator-accepted.**
The 25% digital-peak sweep sounded good without audible distortion. The next
+3 dB trial, reaching signed-16-bit peak 11572 (about 35.3% digital amplitude),
was accepted as "Clean and loud enough". It used four 250 ms, 660 Hz bursts,
30 ms fades, unchanged amplifier gain and explicit PA muting around I2S startup
and teardown. ESP-IDF runtime logging was suppressed to avoid UART0 noise.
The accepted private candidate is audio-probe-20260916/level-plus3db-8ohm-01/;
the experiment-root candidate is an earlier, quieter version.

The requested complete voice test also ran: a previously generated 6.38-second
Azelma introduction, resampled from 24 kHz to 16 kHz without truncation and
scaled to peak 11572, inside a seven-second buffer. Private voice-01/ preserves
its source provenance, source/build, checks and capture. Operator listening
feedback on speech is pending; do not infer intelligibility from transfer success.

The operator reports "8ohm 1w i think"; the speaker rating remains tentative.
Full-scale firmware was prepared but never flashed or played. The digital cap
is not a calibrated watt limit. Speech intelligibility, long-duration playback,
maximum clean power/SPL, echo rejection and real-time transport remain unqualified.

XIAO identity: 303a:1001, serial E0:72:A1:FB:E5:DC. The accepted I2S profile is
16 kHz stereo 32-bit slots, XIAO slave to XMOS; the capture is processed audio.
Offline waveform/payload tests and device cap checks passed (11572 accepted,
+/-11573 rejected without playback). Latest voice transfer: 1,664,000 bytes each
way in 13.005 s, 208,000 input samples/channel, verified checksums, no clipped
input samples. This preloaded diagnostic does not qualify real-time transport.
The original alice-wifi-diagnostic-1 image has been restored with its write hash
verified; Wi-Fi rejoined 192.168.188.63, RSSI -44 dBm; XMOS remains 1.0.8.
The last amplifier command was mute and acknowledged; physical mute readback
is unavailable on this XMOS version. Leave it in effect until an audio owner
actively drives I2S.

[Detailed evidence](hardware/respeaker-lite-audio-evidence-2026-09-16.md) records
all rejected/accepted trials, sources, speaker limits and latency findings.
Raw recordings and source/builds remain private outside the repository under
/home/alice/.local/state/alice-hardware/xiao-e072a1fbe5dc/audio-probe-20260916/.
Aggregate metrics/config/manifests are in artifacts/respeaker/2026-09-16/audio-probe/.
No production ROS, servo or XMOS firmware change, and no VAD implementation,
occurred. User explicitly wants interruption while Alice speaks. Next: obtain
speech listening feedback and qualify echo rejection, then design permanent audio transport/input/
VAD nodes with interruption; measure and reduce warm generation latency. Preserve
the prior ROS migration, existing worktree, artifacts and other-session notes.

**Saved 2026-09-15: ROS 2 Docker implementation and final review complete;
live ROS hardware and physical acceptance remain unavailable/pending.** Preserve the existing worktree and artifacts:
`/home/alice/alice-workspace/.worktrees/streaming-affect-motion`
(branch `feature/streaming-affect-motion`). No merge, push or pruning performed.

- [Runbook](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/ros2.md)
  and [evidence](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/experiments/2026-09-15-ros2-runtime.md).
- Tasks 1–4 have clean implementation reviews. Whole-migration review found
  three Important issues and one documentation correction, fixed at `5ed4e23`:
  accepted action cancellation/fault semantics, first DAC progress expiry, and
  production verification/evidence of loaded model assets. The sole scoped
  re-review closed all four findings with no new breakage. Do not redispatch
  completed work. Existing fork/CMake verification noise is non-blocking debt.
  The review ledger, reports and diffs are archived under
  `artifacts/ros2/2026-09-15/sdd-final-review/`, with a verified archive manifest.
  All six coordinator rulings are also in `coordinator-final-rulings.md` beside
  that directory. Only the redundant plan scratch copy was removed.
- Eight separate idle, non-root, read-only containers use an internal UDP bridge.
  Default simulation needs no devices or model cache. Offline Azelma, selected
  speaker and provisional robot preview are explicit options.
- **Live ROS hardware is unavailable** until a trusted mechanism can establish
  complete host ownership visibility for both Maestro interfaces. Enabled
  hardware overlays/actions still reject before mutation/factories. This reviewed
  coordinator amendment supersedes the earlier hardware-ready command; it adds
  no privileges or host service. Legacy bench CLI behavior is unchanged.
- Clock proof `host-monotonic-zero/v2` requires valid matching current/child
  namespace links within each participant, complete zero monotonic/boottime
  offsets and a common kernel boot. Different containers may have different
  valid namespace IDs. No timestamp translation or safety-bound widening.
- Final verification: 1,079 passed, three skips covered by nine host checks,
  two existing fork warnings; Ruff/mypy (87 files) pass. Eight targeted actual
  container outcomes pass across two retained attempts: default, cancellation
  during preparation/playback/finalization, first DAC loss, model mismatch,
  stalled-TTS cancellation and real offline Azelma. The first offline helper
  failed before inference; only that helper was corrected and that case rerun.
- Final repaired image source SHA256:
  `f7b99195cbd2ae7d72a7f097f88e549ef47654abd268a52db60045f38efa573a`.
  Exact role image IDs, commands and 1,927 hashed evidence files are bound in
  `artifacts/ros2/2026-09-15/final-fix-artifact-manifest.json`. Production TTS
  verifies model/tokenizer/Azelma bytes before loading, binds evidence to the
  loaded worker lifetime and rejects silent reload after worker death.
- Earlier evidence retains its own identities: build07 passed 35 functional
  cases plus four supplements; build08 passed 1,046 tests, default/SIGTERM,
  public launcher and announced speaker-only checks. The subsequent clock/owner
  repair passed 150 covering tests, seven host checks and three container cases.
- Speaker-only Azelma used the selected SN6140 Pulse route and PortAudio DAC:
  185,760 samples played, zero underflows; Maestro/perception were simulated.
  This is stream evidence, not acoustic measurement or physical facial acceptance.
- Latest real-model run with simulated DAC: 185,760 samples played with zero
  underflows, p99 admission 16.965 ms and maximum 21.683 ms. Earlier p99 runs
  were 21–22 ms against the 20 ms target; repeatable timing acceptance remains
  open. First DAC expiry uses the 250 ms predicate; the watchdog observed the
  injected failure after 259.033 ms. Coarse observer stop gaps reach 255–258 ms;
  precise self-crash marker tests measured about 208/209 ms to last mock receipt.
  These measurements do not prove a general exact 250 ms stop or physical PWM cutoff.
- Active simultaneous SIGTERM can leave session handlers unquiesced. Local
  cancellation/evidence completes, followed by explicit failed cleanup after a
  five-second deadline and exit 1. The qualified rclpy workaround is version-bound.
- User-supplied `alice_description` from `codex/robot-description` (`13c2549`)
  passed Lyrical headless preview checks, retaining provisional geometry and
  `alice_preview`. No PWM bridge or added actuation scope.
- No new servo/camera trial ran. The prior visible-motion issue remains unresolved.
- Next work is a separately designed and qualified complete host-ownership
  mechanism before enabling ROS hardware, plus repeatable timing and physical
  acceptance. Simulation, offline TTS, speaker overlay and provisional preview
  are available through the runbook. Preserve legacy calibration and bench CLI.

The following hardware checkpoint remains valid and physically unaccepted; the
ROS migration does not resolve or supersede its pending visible-motion check.

**Saved 2026-09-11: Task 6 software qualified; latest facial tuning awaits a
visible-motion trial.** 879 tests pass. Combined speech/face execution is
implemented; the latest full-blink/final-sad-pose run changed controller PWM but
the camera showed almost no movement. The operator has been asked whether servo
power is still on. Do not infer physical acceptance from PWM readback.

Continue in the preserved worktree:
`/home/alice/alice-workspace/.worktrees/streaming-affect-motion`
(branch `feature/streaming-affect-motion`). Preserve all local artifacts.

- [Current checkpoint](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/checkpoints/2026-09-11-selected-face-speech.md)
- [Experiment and feedback](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/experiments/2026-09-11-selected-face-speech.md)
- [Integration plan](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/superpowers/plans/2026-09-09-speech-emotion-integration-next-session.md)

Accepted mouth baseline remains Azelma, full calibrated range, runtime jaw 0/0
and 100 ms lead. The user requested quicker mouth response, fuller blinking and a
closed-mouth frown for sadness. Candidate tuning and the exact next-run command
are in the current checkpoint. Learned mode remains unfitted neutral fallback;
no live LLM provider is claimed.

Use `git log -1 --format='%h %s' -- SESSION_CHECKPOINT.md` in the preserved
worktree to identify the latest checkpoint commit. Inspect newer changes.
