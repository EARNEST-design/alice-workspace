# Attended incremental speech and selected face trial

Task 6 uses authored smiles/frowns and procedural blinks with incremental Azelma
audio. Learned expression remains unsupported fallback. An explicit attended
run request authorizes this scope under AGENTS.md; the operator is at the master
switch. No repeated readiness phrases or power-off acknowledgments are needed.

## Reviewed scope and caps

Normalized coordinates use each actuator's asymmetric calibrated spans about
Home from `hardware/alice-face-v1.yaml`. These are command limits; no mechanical
velocity or electrical supply margin is claimed. Only channel 6 changes runtime
response. Eye gaze 8/10 and head/neck 0/1/2 are excluded.

| Function | Channel | Calibration min / Home / max (quarter µs) | Trial normalized range | Max step | Max rate /s | Max acceleration /s² | Runtime speed / acceleration |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Mouth | 6 | 4608 / 5059 / 5440 | -1 to 1 | .4 | 10 | 200 | 0 / 0, restore 0 / 11 |
| Lower eyelids | 3 | 2880 / 5626 / 6400 | -1 to 1 | .1 | 1.5 | 8 | retained 0 / 0 |
| Upper eyelids | 4 | 3840 / 6173 / 7232 | -1 to 1 | .1 | 1.5 | 8 | retained 0 / 0 |
| Forehead | 5 | 4032 / 5918 / 6592 | -.3 to .3 | .08 | .8 | 4 | retained 0 / 0 |
| Left corner | 9 | 5120 / 6499 / 6912 | -.8 to .8 | .08 | .8 | 4 | retained 50 / 10 |
| Right corner | 11 | 5120 / 5524 / 6912 | -.8 to .8 | .08 | .8 | 4 | retained 50 / 10 |

The first physical trials used .5 eyelid/.3 corner limits and reached corner
magnitude .243. The operator could not see the expression. The revised corner
extent is .8, within the 100% smile/frown endpoints previously operator-accepted
in `hardware/expression-presets.md`; step/rate/acceleration are unchanged.
Corner/forehead dynamics retain a .18 s response. After the operator reported
incomplete blinking, lids use a .1 s response and full calibrated closure, with
independent caps shown above. Firmware settings are retained.
Minimum per-channel command interval is 40 ms; source and serial progress expire
at 250 ms. The run is bounded to 25 s, audio to 10 s, and the Home ramp to 6 s.
The mouth keeps its accepted .03 s response and 100 ms lookahead compensation.

The optional visible comparison uses `authored-expression-visible-v1.json`
(scale 1, .8 s transition) and `stream-visible-demo-v1.jsonl` (intensity 1).
`sync-responsive-v1.json` reduces envelope attack/release to 10/30 ms; it does
not increase jaw speed/acceleration or change the 100 ms lead. The original
30/60 ms envelope and authored demo remain the comparison baseline. Supply a
full snapshotted config directory through `--config-root`; only the alternate
policy/sync files replace their canonical names in that retained directory.
For the full blink trial, replace `models/face-events-v1.yaml` with
`speech/face-events-full-blink-v1.yaml`: amplitude 1, onset/release 1 s, hold .8 s.
These deliberately slow phases allow complete closure under the command caps.
They are authored trial settings, not a fitted natural blink model.

Add `--sad-hold-s 1.2` for a final negative clause to retain a closed-mouth frown
after the audio has finished. The owner ramps jaw to -1, corners to -.8/+.8 and
other selected channels to Home, holds after commanded rest and matching controller PWM, then returns all to
Home. Hold duration is limited to 2 s; reaching and holding the pose is bounded
to 6 s. The existing 25 s run cap and fault/cancel stops remain active.

## Execution and observation

Run `uv run python -m alice.experiments.face_speech_cli --clauses
config/speech/stream-demo-v1.jsonl --output <new-output-directory>` for a device-free
trial. Add `--play` for speakers with simulated servos. Add `--enable-hardware`
for the explicitly requested attended trial and real speakers.

The hardware root verifies command-port USB vendor/product/serial/interface and
exclusive ownership of both Maestro interfaces. Enabled selected outputs must be
at calibrated Home; disabled outputs may be enabled at Home, recorded separately
because their mechanical starting positions are unknown. No EEPROM writes occur.

Use only the existing robot-facing C525 observer at
`/dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index0` for retained camera
evidence. Verify its USB identity before capture. Do not open the C920 or a
microphone. Preserve camera capture times separately from DAC and serial times.

The serial owner receives a latest-pose mailbox, validates mapped inputs, clips
selected expression ranges and derives integer commands under individual caps.
It records requested pose, host write-start time, sent pulse and current PWM
separately. Successful writes are SENT; PWM readback is not mechanical arrival.
Audio callbacks perform no model inference or serial I/O.

Cancellation, stale input, underflow, watchdog expiry, controller errors and
partial writes abort audio and revoke serial authority. Closure sends no Home or
response-restoration commands. The operator controls power removal on a fault.
Only successful audio completion permits a bounded Home ramp; every selected PWM
must report exact Home before the serial owner restores channel 6 runtime 0/11.

Retain source text/seeds, configs, code/lock snapshots, hashes, PCM/audio timings,
composed proposals, commands/readbacks, robot-camera evidence and operator
feedback. Check mouth alignment while the expression changes and inspect bounds
before describing the physical integration as accepted.
