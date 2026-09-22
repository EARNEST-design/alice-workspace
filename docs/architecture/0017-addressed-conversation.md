# ADR 0017: Wake name and bounded follow-up window

> Wake-bypass and text-only decision rules below are superseded by [ADR 0018](0018-audio-evidence-and-kev-admission.md).


- Date: 2026-09-21
- Status: accepted and implemented for attended bench evaluation
- Authorization: operator selected “wake name + 30 seconds”.

## Decision

Both live modes wait for `Alice`, `愛麗絲` or `爱丽丝` at the start of a final
transcript, optionally after a supported greeting. English requires a boundary;
Chinese names may directly precede Chinese speech. Mid-sentence mentions do
not wake Alice. Outside the window, ordinary speech gets a local WAIT and
makes no MiniCPM, reply-model or TTS request.

Explicit wake opens a 30-second window and can reply without MiniCPM, including
a short acknowledgment for name-only wake. While awake, MiniCPM checks the
utterance and bounded recent conversation; only SPEAK admits a reply. WAIT,
invalid advice, model errors and expiry after the awaited decision prevent
reply/TTS. This replaces the earlier advisory-only behavior.

Successful current-generation output refreshes the window. Capture checks
expiry even without another utterance; expiry clears history. Stop/session end
clear engagement. Cancelled or stale work cannot rearm it. An admitted explicit
wake reply may take time to synthesize; its window refreshes after successful
output. Both live modes can wake and speak when output is enabled.

The dashboard labels Listen “Wake listening” and Conversation “wake required”.
Engagement events distinguish waiting, awake, expired and cancelled. Displayed
duration is a transition value, not a continuous countdown. ROS receives the
same events. Synthetic replay has a private replay-only wake bypass and stays dry.

## Limits and verification

Wake recognition uses final ASR text, not a dedicated keyword model or speaker
identity. Misrecognition can miss the name; a television or another person
saying it can wake Alice. The gate does not establish MiniCPM classification
accuracy. Playback guard remains; no new echo/full-duplex claim is made.

Regressions cover both modes, prefix boundaries, zero downstream requests while
asleep, WAIT/error vetoes, deadline crossing, capture-time expiry and cancellation
ownership. Evidence: `artifacts/conversation/2026-09-21-wake-gate/`.

## Pending-reply guard correction, 2026-09-22

Default mode now pauses speech admission for the entire pending turn (ASR,
decision, reply generation, synthesis and playback), while continuing to drain
microphone frames and update meters. Previously it guarded audible playback
only; new speech during Cantonese generation cancelled the worker, and awaiting
its 359 ms shutdown inside capture overflowed the four-frame queue. This is a
correction to the default non-interruption policy, not a larger/staler buffer.
Endpoint accumulation resets during the guard. The dashboard shows reply_guard
with an explanation. Stop remains available and generation-safe; subsequent
utterances are admitted after completion/tail guard. Both live modes warm both
voices before opening capture; Listen previously omitted this warmup.

Experimental barge-in remains explicitly opt-in and unqualified; its interrupt
path is not repaired by this default-mode guard. See the September 22 experiment.
