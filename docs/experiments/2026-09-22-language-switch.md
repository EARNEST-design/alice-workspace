# Pending reply cancellation and language switching, 2026-09-22

## Trigger and cause

The operator confirmed that the ReSpeaker had been physically muted, resolving
the previous zero-input hypothesis. Recognition then resumed. They reported no
speech output and suspected freezing when switching languages.

Redacted live stage metadata showed a previous reply drained successfully,
followed by a Chinese-tagged ASR turn routed to Cantonese. Its text reply
completed; female TTS started and was cancelled about 0.59 seconds later by
new microphone speech. Worker shutdown took 359 ms inside the capture consumer,
exceeding the four-frame queue's approximate 128 ms capacity. The session ended
with `microphone capture queue overflow`. No human transcript/audio was retained.

Default mode guarded actual playback only. It still admitted input during reply
generation, causing an unintended interruption even with barge-in disabled.
Wake listening also omitted voice warmup, adding avoidable first-reply delay.

## Correction

Default capture keeps reading frames and computing meters throughout the pending
turn but resets/discards endpoint admission until it ends. `reply_guard` is
visible with a message; existing output tail guard remains. Stop and generation
cancellation remain independent. Following utterances can use either language.
Both live modes warm voices before microphone capture. No queue bound, VAD
threshold, digital output cap, wake policy, voice, firmware or device gain changed.
Experimental barge-in remains opt-in and unqualified; this fix does not repair
its separate interruption path.

## Verification

A regression uses the real buffered capture and endpoint components, synthetic
frames and external-dependency doubles with slow speech shutdown. Before the
fix, both modes reproduced capture overflow during pending synthesis. Afterward,
both drain all frames, preserve the Cantonese turn, then accept English. A
separate test caught missing Listen warmup. First red run: three expected
failures and one pass. A test harness was then narrowed from the existing
two-sentence model stub to one sentence to make its voice-count assertion exact;
the original red failures were independently capture overflows.

45 runtime tests and the full 326 conversation/speech suite pass. Scoped Ruff,
format, strict mypy and diff checks pass. Independent review found no important
issues in the correction.

Actual local worker/native speaker switching used one Speaker instance with
unchanged identity-selected ReSpeaker routing and no microphone capture:

| Synthetic phrase | First PCM | Audio duration | Native result |
| --- | --- | --- | --- |
| English introduction | 105 ms | 2.800 s | drained |
| Cantonese introduction | 4671 ms | 3.042 s | drained |
| English return | 119 ms | 2.080 s | drained |

Both-voice warmup took 15.23 seconds. These are software timings, not acoustic
measurements. Operator audibility confirmation was requested separately. The
female voice remains the accepted CosyVoice preset at speed 0.85 and seed 7.
English remains Pocket/Azelma. Model/source/environment pins are in ADR 0016.

Post-reload Wake listening warmed both voices, then a 15-second live window
recorded 119 meter updates, maximum RMS 0.0875, maximum speech probability
0.9863 and four completed ASR finals. There were no error events. No wake was
admitted in this window, so no spoken reply is claimed from it. The service
remained listening with ROS connected and its normal five-minute bound.

## Artifacts and conclusion

Ignored `artifacts/conversation/2026-09-22-language-switch/` contains:
`failure-redacted.json`, `red.log`, `tests.log`, `physical-voice-switch.json`,
`review.md`, `config.json`, `manifest.json`, and post-reload state metrics.
The evaluation uses synthetic diagnostic fixtures, not a training/evaluation
split. Only aggregate microphone and stage metadata are retained. No motor,
firmware, commit, merge or push action occurred.

The observed default-mode overflow is reproduced and fixed; sequential voices
work through the actual speaker path. This does not remove CPU Cantonese
synthesis latency or qualify experimental full-duplex interruption.
