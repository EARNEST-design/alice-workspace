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
