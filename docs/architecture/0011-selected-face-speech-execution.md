# ADR 0011: Selected facial execution for incremental speech

Date: 2026-09-11. Status: implemented; physical acceptance is recorded separately.

## Decision

Task 6 adds a separate `MaestroFaceAdapter` and `FaceCommandStream` for jaw 6,
eyelids 3/4, forehead 5 and corners 9/11. Existing jaw-only adapter guards and the
accepted regression runner remain intact. Full source calibration is pinned;
eye gaze and head/neck channels are excluded from this initial trial.

A fixed profile function uses the accepted jaw settings and conservative
expression limits documented in `hardware/bringup/face-speech-trial-v1.md`.
Expression extent is .5 for lids and .3 for corners/forehead; their command caps
are .08 step, .8/s rate and 4/s² acceleration. All profiles require 40 ms between
commands. Non-jaw firmware response stays unchanged. Only jaw runtime changes to
0/0 and restores 0/11 after normal completion. Electrical margin and mechanical
response remain unmeasured; these are command-space limits.

## Execution boundary

One thread constructs, opens and owns the serial adapter. Inference supplies a
single latest complete selected pose, merging sparse updates. Each channel is
planned immediately before its transaction, using that channel's previous host
write-start timestamp. Sequential USB readback latency cannot become another
channel's command interval. The trajectory algorithm's candidate is quantized to
actual PWM units, checked against step/rate/acceleration over the 2 ms dispatch
window, and committed only from a completed SENT receipt. Observed PWM is
retained separately and is never labeled mechanical arrival or APPLIED.

Quantized endpoint snapping accounts for the response horizon and smaller
calibrated span. Without this, .18 s smoothing could remain two pulse units from
Home forever. The moved-then-Home regression covers that failure.

## Faults and completion

The serial adapter checks a shared cancellation Event before each transaction.
An audio callback underflow sets only this signal; it performs no serial work.
The serial owner handles closure. Pipeline faults revoke face authority before
joining inference or cancelling TTS, and retain the original pipeline error.
The independent 250 ms watchdog also aborts PCM before closing serial. Delayed
inference keeps its source timestamp and cannot make an old frame fresh.

Fault records distinguish a completed target write, an uncertain partial write,
and unavailable PWM. Closing on any fault or cancellation sends no restoration
or Home transactions. Cleanup errors are retained without erasing command and
audio evidence.

Only successful audio completion starts a bounded Home ramp. Every selected
channel must reach commanded zero velocity and exact Home, then all current PWM
values must confirm Home before restoring jaw runtime 0/11 on the serial owner.
Disabled PWM initialization is a separate attended startup event, with unknown
mechanical starting pose. Enabled selected PWM must already be at Home.

## Validation and limitations

Tests exercise actual compact-protocol bytes through a disconnected transport,
variable readback latency, quantization, coupled sparse poses, stale generations,
controller faults, partial writes, cancellation, audio-active watchdogs, delayed
worker cleanup, callback underflow during inference, and full composed replay.
The CLI defaults to simulated speakers and servos; real hardware is explicit.
Authored expressions remain labeled authored, and no fitted emotion package or
live provider-specific LLM adapter is claimed.

## Operator-guided visible comparison

After two clean physical runs, the operator described timing as okay but wanted
faster mouth motion and could not see the other expression. Preserve those runs
as functional execution evidence, not subjective acceptance. The visible
comparison increases only the corner extent to .8, inside the previously
operator-accepted 100% smile/frown poses, retaining .08/.8/4 derivative caps.
Authored intensity/scale become 1 and transition .8 s. Envelope attack/release
become 10/30 ms with no expression jaw bias; jaw speed caps and 100 ms lead stay
unchanged. Original policy and sync files remain the baseline. A CLI config-root
snapshot makes the selected version explicit and reproducible.

The longer Home ramp exposed a repeatable 2.626 ms Python generation-1 garbage
collection pause during command planning. A plan that has already consumed more
than 1 ms is now recorded as discarded before any serial call; sent state remains
unchanged, and the next step recomputes using current elapsed time. Adapter
admission still enforces 2 ms before every write. Repeated skipped plans still
hit the per-channel 250 ms gap limit and fault. The qualifying device-free run
completed after one discarded plan, with all channels Home and zero underflows.


## Full blink and closed-mouth sad pose

The stronger attended trial exposed two separate presentation issues: procedural
blinks reached only about -.4, and speech-driven jaw opening obscured sadness.
The optional full-blink policy now reaches calibrated -1 with a long enough
plateau, using lid-specific limits (step .1, rate 1.5/s, acceleration 8/s²).
The original event config remains unchanged. The foreground jaw still follows
audio during speech. An explicit final-negative trial option asks the trusted
coordinator to enter a bounded post-speech pose only after successful playback.
It closes the mouth, holds the complementary frown corners, clears the lids,
and then returns to Home. This phase has no audio sample/source freshness
claim; its records identify it separately. Calibration, command derivatives,
serial progress, run duration, cancellation and controller checks remain active.
The hold begins after commanded rest and matching latest per-channel PWM readback.
A mismatch restarts the dwell timer; the phase deadline stays fixed. This is
controller-output evidence, not measured mechanical arrival.
