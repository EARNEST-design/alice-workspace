# ADR 0003: Coupled horizontal gaze control

- Status: accepted
- Date: 2026-09-02

## Context

Alice has independent left- and right-eye horizontal servos, but ordinary gaze
control must not expose independent horizontal targets. Independent inference
can produce divergent commands and an unintended cross-eyed pose.

## Decision

Expose one normalized `HorizontalGaze` coordinate above the physical actuator
layer. Expand it atomically to `right_eye_horizontal` and
`left_eye_horizontal`, using the same normalized direction for both calibrated
servos. Perception and learned models may estimate gaze, but they must not emit
independent horizontal-eye commands.

Independent eye control is reserved for a separately named diagnostic mode and
must not be reachable from mimicry or emotion-to-expression inference.

## Consequences

- Mimicry cannot command divergent horizontal eye positions.
- Calibration remains per motor, while production control uses one virtual
  degree of freedom.
- Future trajectory schemas should contain horizontal gaze rather than two eye
  motor coordinates at the policy boundary.
