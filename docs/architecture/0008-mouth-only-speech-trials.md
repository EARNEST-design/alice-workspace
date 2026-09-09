# ADR 0008: Mouth-only speech hardware trial

- Date: 2026-09-09
- Status: implemented; real audio/controller and robot-camera verification completed
- Extends: ADR 0007

All 11 semantic servos map through the original calibrated manifest. The physical
speech root enables only `mouth_open`, channel 6. The operator-approved full
range is -1 to +1 (4608–5440 quarter-microseconds), Home 5059. The earlier
+0.6 profile is retained as experiment history. Normal trajectories retain
maximum step 0.1, rate 2 normalized units/s and acceleration 4 units/s²; firmware
speed/acceleration 0/11 and other channel outputs are unchanged.

Use the existing supervisor, one outstanding command and settled-output Maestro
adapter. A separate audio worker produces DAC-clock frames; the servo consumer
uses the newest frame and does not replay a backlog. Confirmation reports
controller PWM output, not measured mechanical movement.

The operator explicitly superseded repeated physical readiness procedures on
2026-09-09, declaring Alice connected, powered, ready and attended with a hand
at the master switch. An explicit hardware invocation for the requested task is
sufficient. No RUN/OFF phrases or routine power cycling are required. Ordinary
invocations remain device-free; automatic identity, ownership, controller-error
and motion-range checks remain. The operator controls physical power.

Disabled jaw PWM can be enabled at calibrated Home through a fixed jaw-only
startup method with the same explicit adapter token. Record this startup write
separately: its mechanical initial position and velocity are unknown. Observe
Home/error state after a 250 ms wait before supervised playback. The controller
may still report disabled output zero during its first PWM update; retain that
as an observation, never as a positive target acknowledgment.

A positive, in-range initial output need not be Home. Require a second identical
reading 100 ms later; convert through the unchanged calibration. The supervisor
opts into measured startup explicitly and validates complete unique targets.
Seed those positions and zero command velocity when arming, before any recovery
path; the default supervisor still requires Home. The speech root never applies
automatic recovery commands after faults.

Real controller acknowledgments took roughly 40 ms, making the original 6 s
entry deadline too short. The hardware config now permits 8 s per ramp, retaining
the 25 s whole-run bound. `jaw-full-range-v1.json` uses 100 ms between acknowledged
output and the next dispatch to improve visible travel with the existing
acceleration estimator. Its cadence is a diagnostic choice, not a final lip-sync
response fit.

Successful completion requires controller Home and serial closure. Errors or
cancellation revoke authority and stop audio/serial. No power-off acknowledgment
is required. Preserve input/code/config/procedure hashes and timing logs. Supply
rating remains 6 V / 1 A, with electrical margin unmeasured. The operator additionally authorized C525 robot-camera observation. A separate
observer measures lip gap and mouthUpperUp blendshapes; the human camera and
microphone stay unused. Full-range and stronger-envelope speech runs visibly
opened and closed the robot mouth. `sync-hardware-v1.json` uses full_open_rms
0.06 instead of 0.15 so smoothed speech targets cross neutral in both directions.


### Target streaming and runtime servo response

The settled-output adapter delayed large jaw transitions by about 160 ms and
aborted a fast speech run. Speech now has an explicit `--stream-targets` path:
preflight and arming use the supervisor, then a bounded single-jaw trajectory
stream sends one target and reads errors/current PWM without waiting for arrival.
Receipts say SENT, and current PWM is separate from requested targets. Existing
APPLIED permits and settled behavior are unchanged. Completion independently
requires observed Home. Kinematics use host write-start timestamps with a 2 ms
bounded dispatch window; receipt completion drives watchdog progress only.
Variable readback latency and cleanup-fault regressions cover this separation.

The operator then requested servo speed tuning. The controller-resident profile
is speed 0 (unlimited), acceleration 11. An explicit `--fast-jaw-response` option
sets only channel 6 runtime speed/acceleration to 0/0 after observing Home and
restores the reviewed 0/11 profile at normal completion. EEPROM is unchanged. Restoration
failure is reported rather than inferred successful. This removes duplicate
firmware ramping while retaining calibrated software trajectory limits; no claim
about exact mechanical speed follows from controller command timing alone.


The tested `jaw-speech-lead-v1.json` adds 100 ms lookahead for aperture only.
Affect/ownership/current expression remain on the current audio sample. The
completed camera trial showed full movement and zero immediate PWM-target error;
apparent envelope-to-image lag improved by about 110 ms. Camera latency remains
uncalibrated. This profile requires only a short available-PCM buffer in the
incremental architecture; it does not require a complete generated response.
