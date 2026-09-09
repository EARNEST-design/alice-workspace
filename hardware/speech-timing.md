# Speech timing evidence and unknowns

Speech development does not open serial or command actuators.

- The verified `mouth_open` actuator is channel 6. Increasing the normalized
  target opens the jaw; decreasing closes it. Its software range is 4608–5440
  quarter-microseconds with Home 5059. Source: `alice-face-v1.yaml`.
- Mouth corners are separate: channels 9 and 11 have opposite electrical
  polarity. Speech composition preserves the existing expression's corner targets.
- Envelope generation uses 50 Hz **proposal** samples. This is not evidence that
  the jaw or controller can track that rate. The current procedural baseline
  uses a lower cadence for a different purpose.
- Speaker output-device latency, jaw lag, settling, and tracking under speech
  patterns have not been measured. The software does not guess a servo lead time.
- Host speaker playback needs PortAudio and a selected output device. Neither
  speaker selection nor microphone/camera/servo operation is part of synthesis.

An operator-approved timing trial should record DAC-referenced audio time,
requested jaw targets, controller outputs and observed jaw motion separately.
Fit any lead time and smoothing changes against those recordings and bind the
result to the speaker device, controller settings and calibration. Validate the
final composed motion through the existing supervisor before sending targets.
The present implementation provides replay material for that trial, not its
authorization or a measured physical lip-sync claim.

## Azelma replay review, 2026-09-09

The 6.38-second replay in `artifacts/speech/azelma-sync-v2/` exercises the completed
streaming engine in simulation. It exposes physical integration gaps:

- The default speech proposal starts at normalized jaw -1 from Home 0 and
  returns ownership to Home at the tail. These semantic changes are not safe
  physical trajectories. At the proposed 20 ms interval, assuming Home at rest
  one interval before audio start, the initial command difference implies 50/s
  and 2500/s². These are calculated command differences, not observed speeds.
- Even excluding that initial transition, 176 subsequent intervals exceed the
  mouth response prior of 2/s. The 4/s² acceleration prior is also exceeded.
  These priors are unfitted; passing them alone would not qualify hardware.
- `MaestroAdapter.apply()` blocks for controller output settling and the
  supervisor accepts one outstanding request. It cannot be called directly
  from the audio callback as a 50 Hz speech executor.
- The electrical evidence explicitly scopes initial mouth-only identification
  at ±0.05. `max_step` bounds each change, not absolute excursion, and evidence
  scope is not machine-enforced. Composition preserves all expression channels.
  A trial runner therefore needs a final jaw-only allowlist and absolute bounds.

No current reviewed execution root handles speech. The existing identification
root hardwires a slow identification sequence; the streaming bring-up document
allows read-only readiness checks only. A physical timing trial still needs a
bounded command trajectory with gradual Home entry/exit, one pending command and
latest-frame coalescing, supervisor-managed cancellation/recovery, device-clock
and controller/observed-motion logs, and an operator-approved speech-specific
procedure. Do not reuse the full-range mimicry scripts for this trial.
