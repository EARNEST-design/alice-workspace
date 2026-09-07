# ADR 0003: Streaming affect-conditioned face and head motion

- Status: accepted
- Date: 2026-09-07

## Context

Alice needs believable, continuously varying face and head motion conditioned on
a continuous multidimensional affect vector. Repeated motion at a stable intent
must remain alive rather than converge to a fixed pose. The Pololu Maestro and
servos interpolate target changes using controller-resident speed and
acceleration settings, so a high-rate sequence of assumed instantaneous motor
positions would model the physical system incorrectly.

Existing evidence covers facial channels and three head/neck axes: rotation,
tilt, and face up/down. Full-body joints are not yet characterized. Human images
and video may be used only under recorded consent and provenance constraints.
The learning objective is morphology-appropriate affect equivalence, not exact
human landmark imitation or a claim about a person's internal emotional state.

## Decision

Use a controller-aware, hierarchical streaming generator for face and head:

1. Accept a versioned, timestamped continuous affect vector with intensity,
   validity, source confidence, and transition preferences. Optional familiar
   cluster names are descriptive metadata, not categorical targets.
2. Generate overlapping finite horizons while retaining pose, velocity, seeded
   latent state, and recent event history. Emit only a validated prefix before
   replanning.
3. Use a reviewed anchor/spline planner for conservative coarse expression and
   neutral return. A neural state-space model adds bounded residual variation at
   slow and medium timescales.
4. Model sparse micro-events, including blinks and gaze changes, explicitly with
   affect-dependent rates, seeded sampling, and refractory rules.
5. Represent output as sparse target updates plus controller speed/acceleration
   identity. Simulate the actuator response between updates and coalesce targets
   superseded before transmission.
6. Model head/neck expression through semantic primitives: attitude bias, nod,
   shake, tilt, look up/down, and return-to-attention. A learned event policy may
   schedule primitives and choose bounded parameters; a deterministic generator
   converts them to targets for channels 0-2.
7. Keep face and head as separate model segments behind one proposal interface.
   This interface may later admit separately calibrated torso and limb segments.
8. Treat every generated trajectory as a proposal. Independent validation and
   the `SafetySupervisor` retain final authority. Mock and replay remain the
   default adapters.

The runtime falls back smoothly to the current safe anchor or neutral state for
stale or malformed intent, unsupported affect regions, invalid latent state,
deadline misses, discontinuity, schema/calibration mismatch, or constraint
failure.

## Alternatives retained for research

- A rolling conditional transformer may replace the residual dynamics model if
  held-out evidence shows material benefit from long context or multistage
  motion at acceptable latency and continuity.
- A conditional diffusion model may be evaluated if repeated demonstrations
  reveal meaningful multimodality that the state-space model cannot represent.
- A fully learned neck dynamics head may be compared with the semantic primitive
  layer, but must show better believability without reduced interpretability,
  continuity, or constraint performance.
- Anchor-only and procedural stochastic generators remain permanent baselines
  and fallbacks.

All candidates use the same contracts, dataset splits, controller-response
model, safety boundary, human-rating protocol, and reproducibility requirements.

## Consequences

- The model learns commands in the context of the controller's interpolation,
  rather than pretending to command instantaneous poses.
- Controlled variation is reproducible from the intent stream, model identity,
  initial state, and random seed.
- Head gestures remain interpretable and can be evaluated independently from
  facial motion.
- The first implementation cannot claim full-body humanoid generation.
- Raw human media requires explicit consent, permitted-use, retention, and
  access decisions; derived features do not erase those obligations.
- More expressive model families remain available without destabilizing the
  runtime or actuation boundary.

## Verification plan

1. Verify schema, freshness, support, continuity, deterministic replay, model
   state, target coalescing, controller-response, and failure behavior in mock
   and replay tests.
2. Split datasets by participant and capture session and hold out defined affect
   regions. Record unsupported-region behavior explicitly.
3. Compare anchor-only, procedural variation, residual dynamics, and learned
   event scheduling through ablations on identical splits and multiple seeds.
4. Measure target tracking after controller interpolation, command cadence,
   position/velocity/acceleration/jerk, discontinuities, event/refractory
   behavior, seed diversity, and repetitive-motion collapse.
5. Run consented, blinded ratings for affect equivalence, naturalness,
   liveliness, continuity, intentionality, and discomfort. Preserve rating
   distributions and inter-rater agreement.
6. Report face and head results separately. A learned model is promoted only if
   it improves held-out human ratings without worsening controller-limit or
   continuity metrics.
7. Require a separate reviewed procedure and explicit approval before any model
   is evaluated on hardware.

## Relationship to ADR 0002

This decision refines Phase 3 of ADR 0002. Its Phase 1 measurement, Phase 2
system-identification, explicit interfaces, provenance requirements, and safety
gates remain in force.
