# Mimicry bring-up — 2026-09-02

## Scope

This session connected the C920 user-facing camera, C525 Alice-facing camera,
and Pololu Mini Maestro `00037376`. It characterized every configured actuator
over its complete software range and used the results for initial live mimicry
experiments. No camera frames were retained or committed.

## Actuator identification result

The full-range run exercised all eleven configured actuators individually while
holding the others at Home. Each actuator completed three `Home → +100% → Home
→ -100% → Home` cycles in 5% transition increments with ten derived blendshape
observations per plateau.

- 1,430 observations were captured; 1,400 were valid.
- Every controller target matched its requested quarter-microsecond value.
- The Maestro error register remained `0x0000`.
- Full negative `face_pitch` repeatedly reduced face detection to 4/10 frames;
  the immediately following Home plateau recovered to 6/10 before full
  recovery. This is a camera/framing limitation.
- Mouth-open endpoint separation was strongest in `mouthUpperUpLeft` and
  `mouthUpperUpRight`, not MediaPipe's nominal `jawOpen` channel.

Local, ignored artifacts:

- `artifacts/alice-all-actuators-repeatability-100pct-20260902-01/`
- `artifacts/alice-actuator-blendshape-plant-v1/`

The plant artifact records a named 11×52 motor-to-blendshape effect matrix with
signed endpoint effect, repeat standard deviation, signal-to-noise ratio, and
per-feature ownership. It must not be treated as a human-to-robot mapping:
Alice's geometry causes MediaPipe labels and scales to differ from a human face.

## Control abstractions

Two coupled virtual controls were accepted:

- `HorizontalGaze` expands one normalized coordinate to both horizontal-eye
  motors with identical normalized direction.
- `EyelidAperture` expands one coordinate to both eyelid motors. Normal open is
  Home (`0`), closure uses `0…-1`, and `0…+1` is reserved for a separately
  calibrated wide/surprised expression.

Mouth opening works best as a direct calibrated scalar mapping from human
`jawOpen` to the full configured `mouth_open` range. Dense blendshape-error
optimization incorrectly recruited mouth-corner motors because it compared
human and Alice blendshape values without a learned cross-domain intent map.

## Head pose

Blendshapes were rejected as a head-pose signal. MediaPipe facial
transformation matrices provide explicit yaw, pitch, and roll. A six-pose
personal calibration made tracking worse and was removed in favor of direct
frontal-camera Euler angles with fixed angle limits and a center dead zone.

Direct yaw/pitch/roll remained visually imperfect, especially pitch. The
integrated prototype therefore caps head motion while retaining the direct
signal. A robot-side rigid marker, IMU, or a calibrated 3×3 mechanical pose
matrix remains a candidate for closed-loop head control.

## Mimicry prototype state

`tools/integrated_mimicry.py` combines:

- direct full-range mouth opening;
- coupled horizontal gaze;
- coupled normal-to-closed eyelids;
- conservative direct head yaw/pitch/roll;
- passive startup baselines instead of prompted calibration poses.

Mouth-corner and forehead-frown control remain intentionally deferred. These
features overlap strongly with other actuator signatures and need an explicit
human-intent mapping rather than dense same-name blendshape matching.

## Cleanup finding

An early prototype used a fixed 0.5-second pause between commanding Home and
disabling outputs. That was insufficient for the speed-limited neck motor. The
head and integrated launchers now poll every configured channel until its exact
Home target is reported, then disable all outputs and verify zero controller
targets. The final session ended with all eleven Home positions verified,
followed by all eleven outputs disabled and controller errors `0x0000`.
