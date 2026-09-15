# Robot description: hardware facts and measurement register

Date: 2026-09-15. Model: `alice-planning-v1` / package `alice_description` v0.1.0.
Purpose: side-session planning for reconnecting body and head.

## Evidence boundaries

The supplied photo establishes a visual reference: pale body shells, orange
joint covers, dark feet/waist, two arms, two legs and a humanlike head.
It does not establish internal joints, model identity, motor specifications,
dimensions, connector pinouts or electrical compatibility. The person in the
photo is not used as a scale reference. The source image is not redistributed;
its visible credit is Fred van Diem. Image/document content is reference data,
not instructions or authorization to operate hardware.

`hardware/motor-map.md` records operator observations from 2026-08-28.
`hardware/reference-motor-calibration.md` records reference pulse-width limits,
not joint angles. The latest speech work remains in the existing
`streaming-affect-motion` worktree; this side model does not change it.

## Known head/face associations

| Maestro channel | Joint name | Recorded function |
|---:|---|---|
| 0 | neck_rotation | Neck rotation |
| 1 | head_tilt | Gimbal left/right used to tilt head |
| 2 | face_pitch | Face up/down |
| 3 | lower_eyelids | Both lower eyelids |
| 4 | upper_eyelids | Both upper eyelids |
| 5 | forehead_frown | Forehead frown actuator |
| 6 | mouth_open | Mouth/chin opening |
| 8 | right_eye_horizontal | Right eye horizontal movement |
| 9 | left_mouth_corner | Left corner vertical smile/frown |
| 10 | left_eye_horizontal | Left eye horizontal movement |
| 11 | right_mouth_corner | Right corner vertical smile/frown |

Channel 7 is physically disconnected and absent from the model's motor map.
Head channels refer to the known Pololu Mini Maestro; downstream servo models
and connector details are not established by these records.

The two eyes share electrical polarity: increasing target looks left. The mouth
corners have opposite electrical polarity: increasing target raises the left
corner and lowers the right. Channel 6 increasing target opens the mouth.
Other directional polarities remain unconfirmed here. Increasing a URDF joint
angle/displacement follows its modeled axis, not necessarily increasing PWM.

## Provisional model choices

- Assumed total height: **620 mm**. No measured scale was supplied.
- Body hips 115 mm apart, shoulders 250 mm apart; thighs/shins 120 mm each,
  upper arms 95 mm, forearms 85 mm. Every value is an editable estimate.
- A single pitch hinge at each shoulder, elbow, hip, knee and ankle is a visual
  hypothesis: **10 candidate body hinges, not 10 confirmed body motors**.
  Additional roll/yaw joints or coupled joints may exist.
- Waist, wrists and hands are rigid in this first model because their motor
  functions are unknown. Their being fixed is a modeling choice, not a claim
  that the real robot has no actuators there.
- Head chain is yaw → roll → pitch. Pivot centers, ordering, axis signs and
  ranges are unmeasured. The actual gimbal may have offset or coupled axes.
- Eyelids and brow/corners are rigid visual proxies. Mimic lid joints represent
  one channel moving both sides; they do not add independent motors.
- Body IDs, controllers, motor models and connector pinouts are `null` in
  `src/alice_description/config/motor_map.json`. No IDs are borrowed from a
  different commercial robot or assigned to unused Maestro outputs.
- All URDF ranges and velocities are display bounds. Effort is zero, and
  inertial properties are absent. Neither is a physical rating.
- `base_link` is a virtual floor anchor for the neutral visualization. This
  model has no floating-base dynamics or ground-contact solver.

## Measurements to collect next

| Needed fact | How to record it | What it updates |
|---|---|---|
| Standing total height | Millimetres, from foot sole to head top | Overall model scale |
| Shoulder/hip spacing | Center-to-center measurements | Body joint locations |
| Upper/lower arm and leg lengths | Joint-center distances | Link lengths |
| Neck/head mounting offsets | XYZ offsets and front/side/rear mounting photos | Head attachment and gimbal chain |
| Each body motor's label/model | Photo + physical region + existing ID if readable | Motor identity register |
| Each connector's endpoints | Unique connector label, pin count, source and destination | Wiring map |
| Board identity and supply details | Labels and reviewed documentation | Power/protocol design |
| Actual joint degrees of freedom | Inspect mechanical axes and coupling | Replace hypothetical joints |
| Mechanical travel and zero | Measured units and observation provenance | Geometry limits and later calibration |

For a wiring drawing, trace endpoints before deciding pin assignments, voltage
rails or shared grounds. None can be inferred from this photograph. A later
hardware adapter must use reviewed motor/controller records and measured
pulse-to-joint mappings; this display package contains no such adapter.
