# ADR 0002: Three-phase emotion-to-expression learning pipeline

- Status: proposed
- Date: 2026-08-31

## Context

Alice needs a production pipeline that can receive an emotion representation and
produce safe, expressive actuator trajectories. Before selecting or training a
sequence model, the project must establish whether the robot-facing camera can
measure Alice's facial changes reliably and how each actuator affects those
measurements.

The prototype in `venetanji/alice-feedback` demonstrated a useful feedback idea:
observe MediaPipe blendshapes on a human and on Alice, estimate a local
motor-to-blendshape Jacobian, and use that estimate to improve motor commands.
The prototype does not provide the safety isolation, semantic schemas,
reproducible datasets, or evaluation needed for production. Its licensing must
also be clarified before source code is copied.

The current workspace has an operator-verified channel map and partial
calibration evidence, including a previously jammed left-eye linkage. It does
not yet have executable perception, data capture, simulation, or model code.

## Decision

Develop the system in three gated phases. Each phase produces a versioned
dataset and evidence report before the next phase begins. Hardware access is
absent by default and must never be inferred from device availability.

### Phase 1: Passive robot-face perception

Validate the attached webcam as a passive measurement instrument while Alice
remains stationary.

The perception adapter emits a `BlendshapeObservation` containing:

- capture and monotonic timestamps;
- camera and run identifiers;
- detector name, model version, and ordered category names;
- named blendshape scores and per-observation face confidence;
- image dimensions and non-secret camera settings;
- validity state and a reason when no usable face is detected.

The stored dataset includes an immutable run manifest, environment and package
versions, configuration, artifact checksums, and a short conclusion. Derived
blendshape observations are stored by default. Raw frames or video require an
explicit retention, consent, and privacy decision in the run configuration.

Passive experiments measure:

- detection rate and loss-of-face behavior;
- warm-up drift and stationary temporal variance;
- sensitivity to lighting, camera placement, focus, and exposure;
- repeatability after restarting the camera and process;
- correlations and effective dimensionality of the robot-face observations.

Phase 1 passes only when a fixed camera setup yields stable, named observations
with documented variance and repeatability. Failure is an experimental result;
the system must not compensate by moving hardware.

### Phase 2: Guarded actuator system identification

Measure how actuator commands change Alice's observed blendshapes. This phase
uses a dedicated experiment runner and an actuator interface with separate
mock, replay, and Maestro implementations. Mock mode is the default.

Hardware mode requires all of the following:

- an explicit command-line enable flag and reviewed run configuration;
- operator confirmation immediately before the run;
- the versioned semantic motor map and calibration checksum;
- a documented emergency-stop and servo-power removal procedure;
- physical clearance and inspection of the previously jammed channel 10
  linkage;
- confirmation that no competing serial or ROS actuator process is running;
- controller identity, error-register, and Home-position preflight checks.

The initial experiment moves one actuator at a time through a conservative,
reviewed sequence:

1. Home and establish a stable visual baseline.
2. Move to a small positive offset with configured speed and acceleration.
3. Wait for both actuator settling and visual settling.
4. Capture repeated observations.
5. Return Home and re-establish the baseline.
6. Repeat at the corresponding negative offset.
7. Return Home before advancing to another offset or actuator.

Larger offsets, coupled-actuator experiments, and full-range characterization
require separate reviewed configurations. They are not automatic extensions of
the initial procedure.

Every command passes through a `SafetySupervisor` that validates schema and
calibration identity, finite values, actuator limits, command age, permitted
step size, velocity, acceleration, run state, and watchdog state. Missing or
invalid input fails closed. Abort handling commands the reviewed safe state when
communication is available, stops issuing commands, records the fault, and
requires operator acknowledgement before re-enablement.

The phase-2 dataset records requested and observed actuator positions, named
blendshape observations, settling decisions, controller faults, camera state,
and all relevant configuration identities. Analyses estimate:

- neutral and within-position variance;
- repeatability across cycles and sessions;
- signal-to-noise ratio for every actuator/blendshape pair;
- monotonicity, saturation, asymmetry, and hysteresis;
- actuator coupling and cross-effects;
- local Jacobians with uncertainty across operating regions;
- timing, settling, and tracking latency.

Phase 2 passes when the useful and unreliable observable dimensions are known,
safe command limits are encoded, and a held-out repeat run reproduces the main
actuator effects within documented tolerances.

### Phase 3: Emotion-conditioned actuator sequence model

Build the model only after phases 1 and 2 establish measurement quality and
actuator dynamics.

The external input is an `EmotionIntent`, not a claim about a person's internal
state. It contains:

- a versioned emotion/affect vector schema;
- intensity and optional style controls;
- desired onset, hold, and release timing;
- timestamp, validity duration, and source identity;
- uncertainty or confidence when supplied by an upstream component.

The model output is an `ActuatorTrajectory`: a timestamped sequence of
semantically named normalized actuator targets with schema, model, and
calibration identities. It is a proposal, never direct hardware authority. The
`SafetySupervisor` remains the final authority before the actuator adapter.

Start with two baselines:

1. operator-authored expression keyframes plus bounded interpolation; and
2. a small feed-forward or recurrent model with explicit temporal features.

A transformer is a candidate when the dataset demonstrates that long-range
temporal context, multi-stage expressions, or variable-length conditioning
materially improves held-out performance. Model selection is based on measured
quality, latency, data volume, and deployability rather than assumed complexity.
The model interface therefore supports variable-length sequence models without
committing the first implementation to a transformer.

Training is offline and reproducible. Every experiment records dataset and
split identities, provenance and consent constraints, configuration, random
seeds, code revision, dependency lock, model artifact checksum, metrics, and a
short conclusion. Splits are grouped by capture session so adjacent frames do
not leak between training and evaluation.

Evaluation includes:

- constraint violations before and after safety supervision;
- trajectory smoothness, latency, overshoot, and settling;
- weighted robot-face blendshape tracking on held-out sessions;
- repeatability across lighting and camera restarts;
- behavior under missing, stale, malformed, and out-of-distribution inputs;
- operator-rated expression recognizability under a separately documented
  protocol, if human evaluation is later approved.

## Component boundaries

```text
Camera -> PerceptionAdapter -> BlendshapeObservation -> Capture/Replay Store
                                             |
EmotionIntent -> SequenceModel -> ActuatorTrajectory
                                      |
                                      v
                             SafetySupervisor
                                      |
                                      v
                         ActuatorAdapter (mock by default)
                                      |
                                      v
                                  Alice face
                                      |
                                      +----> robot-facing Camera
```

The phase-2 experiment controller consumes observations and actuator status but
does not bypass the safety supervisor. Perception, modeling, supervision, and
actuation remain independently replaceable and testable.

## Error and state model

Hardware-capable processes use explicit states: `DISARMED`, `PREFLIGHT`,
`ARMED`, `RUNNING`, `ABORTING`, and `FAULTED`. Only `RUNNING` accepts experiment
steps. Device discovery never changes state. Loss of camera observations pauses
or aborts according to the reviewed run configuration; it never reuses an old
observation as though it were current.

All cross-component messages reject unknown schema or calibration identities,
wrong dimensions, non-finite values, and stale timestamps. Simulation and
hardware modes have distinct types/configuration and prominent run metadata so
fallback cannot occur silently.

## Alternatives considered

### Port the prototype loop directly

Rejected. It combines perception, online training, calibration, UI, and direct
actuation; it can move hardware during construction/calibration; and it lacks
the provenance and failure-state controls required here.

### Begin with a transformer

Rejected as the initial implementation. The required dataset size, temporal
horizon, conditioning structure, and latency are not yet known. The trajectory
contract remains compatible with transformers once evidence supports one.

### Use only hand-authored expression presets

Retained as a phase-3 baseline but rejected as the complete system. Presets are
easy to audit and useful for safety validation, but they do not characterize
mechanical variation or support flexible temporal expression generation.

## Consequences

- Model development waits for measurement and system-identification evidence.
- Initial work produces infrastructure and datasets rather than immediately
  visible autonomous expressions.
- Hardware experiments are slower because they require explicit review and
  operator presence.
- Named, versioned contracts allow perception and sequence-model choices to
  evolve without changing the safety and actuator boundary.
- Transformer experimentation remains possible without making it an
  unsupported architectural dependency.

## Verification plan

1. Contract tests reject reordered, missing, non-finite, stale, and mismatched
   observations and trajectories.
2. Replay tests reproduce phase-1 statistics from immutable fixtures.
3. Mock-actuator tests exercise every state transition, timeout, watchdog,
   range violation, and abort path without hardware.
4. Disconnected protocol tests verify exact Maestro bytes and never open a real
   device.
5. Phase-1 acceptance is recorded before any phase-2 hardware run is proposed.
6. Every hardware run links its reviewed procedure, manifest, calibration, and
   results; ordinary tests and CI cannot enable hardware.
7. Phase-3 candidates are compared against keyframe and simple learned
   baselines on held-out session-grouped splits.

## Follow-up decisions

- Define the concrete version-1 message schemas and package boundaries.
- Define phase-1 camera placement and acceptance thresholds from pilot data.
- Resolve the prototype repository license before copying source.
- Approve a dedicated phase-2 bring-up procedure before actuator execution.
- Select the version-1 emotion vector based on the intended upstream source and
  expressive vocabulary.
