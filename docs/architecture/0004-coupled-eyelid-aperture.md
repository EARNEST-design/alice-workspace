# ADR 0004: Coupled eyelid aperture control

- Status: accepted
- Date: 2026-09-02

## Context

Alice has separate upper- and lower-eyelid motors. Ordinary expression control
should coordinate them as one eyelid aperture instead of allowing a learned
policy to produce incompatible positions.

## Decision

Expose one normalized `EyelidAperture` coordinate with explicit semantics:
`-1` is fully closed, `0` is the calibrated Home pose, and `+1` is fully open.
Expand it atomically to `lower_eyelids` and `upper_eyelids` using the same
normalized position. Their distinct pulse calibrations remain confined to the
hardware manifest.

Independent upper/lower-eyelid control remains available only to calibration
and diagnostic experiments, not mimicry or emotion-model output.

## Consequences

- Production policies cannot command contradictory eyelid positions.
- Blink and aperture trajectories become one expressive degree of freedom.
- Per-motor calibration and system-identification evidence remain usable below
  the virtual control layer.
