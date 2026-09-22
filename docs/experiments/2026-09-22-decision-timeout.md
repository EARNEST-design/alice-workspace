# Remote decision timeout, 2026-09-22

## Observed failure

The user reported recognized speech without an answer. The live latest turn had
an English final, a matched wake name, 1.11 s of single-speaker speech, zero measured
overlap and successful quality analysis. Admission was `unavailable` with “Decision
exceeded its time budget”; no reply or TTS request followed. Thus this incident
stopped at the remote admission deadline, before synthesis or playback.

No human transcript or microphone audio is retained in these artifacts. The exact
latest request was reconstructed in memory for two timing probes only. It completed
with SPEAK in 1.376 and 1.129 s. A synthetic reply between these probes completed in
0.930 s, first text 0.560 s. Three synthetic admission requests took 2.001, 0.917 and
1.724 s and returned SPEAK. These later successes do not establish whether original
slowness came from server load, cache state, network or other scheduling.

Before this change was deployed, the operator retried and confirmed “She answers
now.” Redacted stage observation also showed reply and TTS completion. The recovery
preceded this fix; do not attribute it to the new deadline.

## Repair and tradeoff

Increase only remote Qwen's total/request deadline from 3 to 8 seconds, retaining
its 3-second connect bound. The local MiniCPM and Kev experimental budgets stay at
3 seconds. No prompt, model, threshold, audio quality, voice or device change.
Fast decisions return as soon as available. Slow decisions can now hold new-turn
admission for up to 8 seconds while capture keeps draining. Strict completed label
validation, Stop cancellation, generation checks and follow-up expiry remain.

This is bounded resilience for transient remote delay, not a server-side latency
repair. The earlier frozen maximum-size Cantonese input required 5.757 seconds, so
the old budget already excluded a known slow workload. The same frozen English and
Cantonese synthetic cases in the updated client returned WAIT in 1.336 and 0.446 s;
a short greeting returned SPEAK in 1.094 s. Cache state is uncontrolled: these are
smoke tests, not evidence that changing a timeout accelerates inference.

## Verification and service

Two delayed 3.1-second SPEAK/WAIT tests first reproduced the old timeout failure.
They now pass; a scaled stalled-server test verifies cancellation at the configured
budget. All 90 model/runtime tests passed in 6.85 s, including existing Stop and wake
expiry checks. Ruff lint/format, strict mypy on models.py and git diff --check pass.
Independent scoped review approved with no important findings. No broader model
accuracy or room robustness qualification is claimed.

Restarted the exact owned dashboard with unchanged arguments and the new code.
Execution session 48265; log:
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/bench-service-decision-timeout-20260922.log`.
The service returned to wake listening with both accepted voices warm, live output
enabled, hosted Qwen ASR, remote Qwen admission/replies, and ROS connected. An extra
Start request was rejected while a session was already active; observation confirmed
listening, so no duplicate capture was started. Five-minute session bound remains.
Post-reload stage observation is captured separately in reloaded-state.json.

Kev remains installed and stopped: tested 0.6B/0.8B/4B variants each scored 8/14 in
its synthetic development screen; 4B missed five of six valid calls and took up to
32.7 seconds. Qwen passed a separate 12-case full-evidence screen. They are distinct
screens, not directly comparable accuracy estimates. MiniCPM full-evidence screen
was 6/12, all SPEAK. See ADR 0018 for the active backend decision and this amendment.

Ignored artifact directory: `artifacts/conversation/2026-09-22-decision-timeout/`.
Retained files contain synthetic probes, redacted stage/timing metrics, regression
logs, config and review. No human audio/transcript, credentials or embeddings saved.
No motion, firmware, model download, commit, merge or push.
