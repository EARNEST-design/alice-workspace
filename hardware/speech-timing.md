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

At that replay review, no reviewed execution root handled speech. The existing identification
root hardwires a slow identification sequence; the streaming bring-up document
allows read-only readiness checks only. A physical timing trial still needs a
bounded command trajectory with gradual Home entry/exit, one pending command and
latest-frame coalescing, supervisor-managed cancellation/recovery, device-clock
and controller/observed-motion logs, and an operator-approved speech-specific
procedure. Do not reuse the full-range mimicry scripts for this trial.

## Mouth-only executor qualification, 2026-09-09

`alice-jaw-trial` now provides the bounded trajectory and separate audio/serial
workers. All 11 semantic servo names map through the original manifest; the
first physical mode accepts only channel 6. The procedure is
`hardware/bringup/mouth-speech-trial-v1.md`, config
`config/speech/jaw-trial-v1.json`, and decision ADR 0008.

The real-audio/mock-servo run `artifacts/speech/jaw-mock-audio-3/` completed in
9.694 s including ramps: 233 commands, 153 during speech, 318 observed audio
frames, controller target range -1 to +0.300418 and final Home 0. Mock ACK
intervals were 40.249–42.561 ms. The limiter smooths the waveform; it does not
promise to reach every raw aperture peak. End-to-end synthetic tests also pass
with 25 ms delayed ACKs. Unsupported latency still aborts.

This is speaker-clock and mock-controller evidence. No serial device was opened
for these runs, and physical jaw lag remains unmeasured. This was the original readiness policy; the operator subsequently superseded
repeated prompts with an explicit run request under the standing powered setup.

## Physical and camera verification, 2026-09-09

After the operator simplified readiness, actual serial trials revealed disabled
startup PWM and a roughly 40 ms controller update/acknowledgment cadence. The
adapter now records a transient zero output without a schema error and enables
only the disabled jaw at Home. A stable positive in-range start is measured twice
and passed to the supervisor instead of assuming Home. Ramp deadline is 8 s in
the trial config; the 25 s whole-run cap remains.

The first completed speech runs had no positive jaw excursion; the operator
reported closure only. Full-range calibration was then explicitly authorized,
and C525 images plus the existing MediaPipe detector independently observed it:

- `artifacts/speech/jaw-full-range-camera-1/`: commanded -1 to +1; detected lip
  gap 7.4–38.3 px; 158/158 face detections valid. mouthUpperUpLeft/Right ranged
  approximately 0.03–0.65, while jawOpen stayed below 0.0013.
- `artifacts/speech/azelma-full-range-camera-1/`: stronger envelope (full_open_rms
  0.06), full-range profile, 74 acknowledged commands/41 during speech,
  controller speech range -1 to +0.5764, lip gap 7.6–32.7 px during speech,
  129/129 face detections valid, audio completed and controller returned Home.
  Total run 11.24 s. No controller/audio faults.

The observer sampled the existing robot-facing C525 and saved robot-only images,
derived scores, read-time monotonic timestamps and model hash in ignored local
artifacts. The human-facing camera and microphone were not opened. Camera
read-time timestamps are not sensor-exposure timestamps; no precise physical
lip-sync lag is asserted. The full-range speech profile is now the recommended
starting point for timing refinement and incremental streaming.

## Target streaming and servo speed investigation

`artifacts/speech/azelma-streamed-camera-1/` completed with 166 sent targets,
153 during speech, full -1…+1 excursion and 7.51–38.07 px observed lip gap.
All 88 frames detected Alice. Mean serial write/readback duration was 1.017 ms
(range 0.614–4.423 ms); audio and controller Home completed in a 7.106 s run.
The operator reported improved response with a remaining visible delay.

The device-resident settings export in `artifacts/speech/servo-speed-check/`
confirms jaw channel 6 speed 0, acceleration 11. Speed is already unlimited;
acceleration 11 imposes firmware ramping. In the previous run, immediate current
PWM differed from requested speech targets by a mean 188.9 quarter-microseconds
(maximum 581). This measures controller-output tracking, not mechanical lag.
The next explicit runtime 0/0 trial isolates the firmware ramp while retaining
the same PCM, envelope and software motion caps.


## 100 ms mouth lead trial, 2026-09-09

`artifacts/speech/azelma-lead100-camera-1/` used `jaw-speech-lead-v1.json`,
`--stream-targets --fast-jaw-response` and the same retained Azelma PCM. It
completed in 6.878 s: 166 sent targets, 153 during speech, full -1…+1 command
range, all immediate PWM readbacks equal to their targets, and 0.860 ms mean
serial round trip (0.593–2.557 ms). Camera detections were valid on 85/85 frames;
speech lip gap spanned 7.27–37.14 px. Audio and Home completed, and the serial
owner restored runtime speed/acceleration 0/11 before closing. A new read-only
settings export exactly matched the pre-trial export; EEPROM remained unchanged.

Apparent envelope-to-image cross-correlation peaked at 80 ms (r=0.931), down
from 190 ms without lead. Command-to-image remained 120 ms (r=0.981), consistent
with advancing commands rather than changing the mechanical/camera response.
These estimates include unknown camera latency and are not precise acoustic
lip-sync measurements. The 100 ms lead is retained as an explicit tested profile;
normal defaults remain zero. The operator confirmed the final 100 ms lead trial: “Looks aligned”.
This is subjective acceptance for the tested clip and attended setup.

The metrics include analysis-script/input hashes; the trial manifest includes
source, configuration, procedure and input hashes. Robot-only camera imagery
remains in ignored local artifacts. No human camera or microphone was opened.
