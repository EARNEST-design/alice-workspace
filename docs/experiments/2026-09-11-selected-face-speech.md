# Incremental speech and selected-face execution

Task 6 follows the Tasks 1–5 checkpoint at `065b323` in the preserved
`feature/streaming-affect-motion` worktree. The operator explicitly requested
Task 6 and reported the servos powered. That authorizes the documented selected
facial scope under the standing attended-bench policy.

## Configuration

The retained two-clause fixture uses Azelma with seeds 29/30 and contrasting
positive/negative authored affect. No fitted emotion package or live LLM
provider is claimed. `face_profiles()` derives individual command caps from the
reviewed calibration and proposal range; full snapshots are retained per run.
The accepted jaw lead (100 ms), full range and runtime 0/0 remain intact.

Initial channels: mouth 6, eyelids 3/4, forehead 5, corners 9/11. Eye gaze 8/10 and
head/neck 0/1/2 are excluded. The fixture moves eyelids and corners; forehead
remains at its authored neutral target. All outputs stay separate from physical
position measurements. Details: [ADR 0011](../architecture/0011-selected-face-speech-execution.md)
and [trial procedure](../../hardware/bringup/face-speech-trial-v1.md).

## Software verification

Baseline: 845 tests passed. Initial implementation verification: **871 tests passed**
with two existing fork deprecation warnings; Ruff and mypy passed (85 source
files). Independent review found and helped qualify fixes for quantized Home
settling, faulting transaction evidence, and immediate audio/face revocation.

Tests include per-channel variable USB latency, integer-PWM bounds, sparse
coupled expressions, unselected channels, cancellation/stale sources, controller
errors, partial writes, watchdog expiry, old frames after inference, callback
underflow during inference, and slow TTS cancellation. Every Home confirmation
precedes jaw response restoration; fault closure performs no additional writes.
Retained red/green evidence is under
`artifacts/speech/face-integration-2026-09-11/`.

## Real TTS with simulated devices

`azelma-simulated-1/` completed with 185,760 samples at 24 kHz (7.74 s), zero
underflows, a 48,000-sample maximum ring depth, first PCM at 2.892 s and first
simulated audio at 2.931 s. All six channels ended at exact commanded Home with
Home PWM observations; each sent 286 commands including waiting and Home phases.
These observations are simulated, not physical evidence.

Maximum sent jaw rate/acceleration: 9.784/s and 195.837/s² (caps 10 and 200).
Maximum expression rate/acceleration: .789/s and 3.983/s² (caps .8 and 4).
Largest command interval: 43.11 ms. Lids reached approximately -.404; left
corner ranged -.242 to +.223 with complementary right-corner polarity. Forehead
stayed neutral. Per-channel summaries, requested poses and actual integer PWM
commands are retained separately from the raw generated proposals.

## Physical observation

Three physical trials completed before full-blink tuning, all with zero audio
underflows, selected Home confirmation and restoration of jaw runtime 0/11.
Each used the same 185,760-sample (7.74 s) Azelma audio. Camera scope was the
existing robot-facing C525 only, with its stable USB identity checked before
capture and the retained MediaPipe model digest
`64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`.
No microphone or human-facing C920 is selected.


| Run directory | First DAC / first-clause final PCM | Camera detections | Operator observation |
| --- | --- | --- | --- |
| `azelma-hardware-camera-1` | 3.176 / 3.528 s | 149 / 149 | Requested repeat; headphones were off. |
| `azelma-hardware-camera-2` | 3.161 / 3.541 s | 147 / 147 | Timing looked OK; wanted quicker mouth and visible expression. |
| `azelma-visible-hardware-camera-1` | 3.191 / 3.561 s | 154 / 154 | Blink incomplete; sadness needs mouth closed. |

The visible comparison raised authored intensity/anchor scale to 1, shortened
transition to .8 s and expanded corner range to .8 while retaining corner
rate/acceleration limits. The jaw envelope changed from 30/60 ms attack/release
to 10/30 ms, with expression jaw bias disabled. Jaw caps and 100 ms lead stayed
unchanged. It reached corner targets near ±.8, lid targets near -.403, and all
sent derivatives remained within their profiles. These runs establish controller
execution and operator feedback, not final visual acceptance of all expressions.

## Scheduling correction retained with tuning

Two device-free visible trials failed in the longer Home ramp due to a >2 ms
dispatch overrun. A third diagnostic run captured a 2.626 ms Python generation-1
garbage-collection pause during target planning. The correction discards a plan
that has consumed >1 ms before I/O, preserves sent state and replans from the
next current clock. Adapter dispatch expiry and the 250 ms channel gap remain
enforced. The succeeding `azelma-visible-simulated-3` recorded one discarded
unsent plan and completed; the subsequent visible hardware trial recorded none.
All failed/diagnostic artifacts are retained, without relabeling them as passes.

## Full blink and final sad pose

The operator's latest feedback is preserved verbatim in
`azelma-visible-hardware-camera-1/operator-feedback.json`. The next candidate
uses full calibrated lid closure (-1), step .1, rate 1.5/s, acceleration 8/s²,
response .1 s; original firmware settings are unchanged. Its versioned authored
blink has 1 s onset/release and .8 s plateau so the bounded command can arrive.
The new optional 1.2 s final sad pose closes the jaw, retains corners -.8/+.8 and
clears other selected channels only after successful audio completion. The dwell
starts after commanded rest and matching latest controller PWM; it is not a
mechanical arrival measurement. Cancellation/faults bypass this phase and Home.

`azelma-full-blink-simulated-1` completed with both lids at exact -1 and 58
post-speech commands per channel, all selected Home and zero audio underflows.
A delayed-readback regression subsequently strengthened the dwell condition to
require controller PWM arrival. Its original commanded-rest implementation is
preserved in that run's code snapshot; later trial snapshots include the fix.

No microphone, calibrated camera-buffer latency or acoustic onset measurement is
claimed. Camera landmarks on the robot support visual review; PWM feedback does
not measure mechanical velocity. Forehead remained neutral because the current
authored preset has no forehead displacement. Learned emotion and a live LLM
provider remain separate work.


### Latest controller run: physical validation inconclusive

`azelma-full-blink-hardware-camera-1/trial` completed: 7.74 s PCM, zero
underflows, first DAC 3.162 s before first-clause final PCM 3.506 s, all selected
controller Home confirmed and jaw runtime restored. There were 340 commands per
channel, maximum gap 48.80 ms, no discarded plans. Both lids reached -1 for 16
commands each with matching PWM; the requested post-speech hold was 1.2 s.
Independent sent-command limits passed: jaw rate 9.253/s, acceleration 196.004/s²;
lids rate ≤1.470/s, acceleration ≤7.900/s²; corners rate ≤.797/s, acceleration ≤3.966/s².

However, visual inspection of initial, commanded-full-blink and commanded-sad
frames shows the face staying almost unchanged. All 177 camera observations
detected the robot, but lip-gap estimates ranged only 13.10–17.90 px. **This run
does not validate physical motion.** The operator has been asked whether servo
power remains on. Do not infer motor power or mechanical arrival from controller
PWM. The latest tuning remains pending an attended run with visible motion and
operator feedback.

Final software checks: **879 tests passed**, two existing fork warnings, Ruff
clean and mypy clean (85 source files). Independent review found no remaining
blockers after adding PWM-qualified dwell. Review notes and immutable per-run
code/config snapshots are retained in the artifact directory.
