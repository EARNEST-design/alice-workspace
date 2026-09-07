# Streaming Affect-Conditioned Motion Design

## Objective

Build a research architecture that produces believable, continuously varying
face and head motor targets for Alice from a time-varying, continuous
multidimensional affect vector. The first system covers the currently evidenced
facial and head/neck channels while preserving an interface for later,
separately calibrated body segments.

Believable means that consented human raters judge the robot's response as
natural, lively, continuous, intentional, and affect-equivalent to a reference
or requested coordinate. It does not mean copying human facial geometry or
inferring a person's true internal emotional state.

## Scope

### Included

- Continuous affect conditioning with optional labels for known clusters.
- Indefinite streaming through stateful, overlapping finite-horizon generation.
- Seeded, controlled natural variation at multiple timescales.
- Facial expression, gaze and blink behavior, and semantic head/neck gestures.
- Controller-aware learning and simulation using target, speed, acceleration,
  reported position, and elapsed time.
- Provenance-aware datasets, reproducible training, offline evaluation, and
  consented blinded human ratings.
- Mock and replay integration with a trajectory proposal contract.
- Explicit comparison gates for alternative model families.

### Excluded from the first implementation

- Full-body motion before additional joints and dynamics are characterized.
- Claims that affect coordinates describe a person's ground-truth emotion.
- Literal human-landmark-to-robot-landmark imitation.
- Online learning during hardware operation.
- Direct model authority over actuators.
- Hardware model trials without a separate reviewed procedure and approval.

## Research operating mode

This work begins as exploratory research, so process must scale with risk and
with the strength of the claim. Records exist to make useful results
reproducible and unsafe actions explicit, not to require production ceremony for
every notebook, plot, or discarded idea.

- A disposable mock or replay spike needs a small captured config, seed where
  relevant, input-data reference, and a short keep/discard note.
- A result used to choose an architecture or reported as evidence additionally
  needs immutable split and artifact identities, comparable metrics, and a
  concise conclusion.
- A retained dataset needs provenance and consent constraints. Identifiable
  human media always keeps its access, retention, and permitted-use record.
- A human study needs a stable rating protocol when its results support a model
  claim; informal lab feedback may guide iteration but is labeled as such.
- A hardware run keeps the repository's explicit actuation approval and bring-up
  boundary. Routine model hacking remains in mock, simulation, or replay.

Model cards, extensive reports, and promotion reviews are created for retained
candidates and decision-grade results, not for every exploratory run. Templates
should stay short, automation should fill mechanical metadata, and a negative or
discarded experiment may end with one paragraph.

## Affect representation

`AffectIntent` contains:

- a schema identifier and ordered continuous vector dimensions;
- intensity and optional transition preferences;
- source identity and optional source confidence;
- capture timestamp, monotonic receipt timestamp, and validity duration; and
- optional descriptive cluster annotations.

The initial dimensions will be versioned rather than embedded permanently in
the model interface. Valence, arousal, and dominance are the starting candidate,
but the dataset protocol may add independently justified continuous dimensions
through a new schema version. Cluster names help researchers browse and stratify
the space; they are neither exclusive classes nor primary training labels.

An `IntentFilter` validates identity, dimensions, finite values, freshness, and
transition rate. It estimates dataset support for the requested coordinate and
smooths valid changes. Unsupported coordinates produce an explicit status and
fallback behavior rather than unconstrained extrapolation.

## Runtime architecture

```text
AffectIntent stream
        |
        v
IntentFilter ---- support estimate / fallback status
        |
        v
StreamingMotionGenerator
  |-- AnchorPlanner
  |-- ResidualDynamicsModel
  |-- FaceEventGenerator
  `-- HeadGestureScheduler -> HeadPrimitiveGenerator
        |
        v
TargetUpdateHorizon + GeneratorState
        |
        v
TrajectoryValidator -> SafetySupervisor -> mock/replay by default
        |
        `---------------- controller-response observation/replay
```

The generator plans an overlapping finite horizon and emits only a short prefix.
Its state contains the last accepted target and reported pose, estimated
velocity, affect-filter state, latent style state, pseudorandom-generator state,
and recent event history. Given the same intent stream, initial state, model,
calibration, controller settings, and seed, replay is deterministic.

The face/head implementation is a segment implementing a generic motion-proposal
interface. Later torso or limb segments get separate calibration identities,
state, constraints, and validation. Their addition does not change the affect
contract or grant any model actuation authority.

## Controller-aware target generation

The Pololu Maestro and servos interpolate between target updates according to
controller-resident speed and acceleration settings. Therefore the learned and
evaluated output is a sequence of sparse target updates, not a high-rate claim
about instantaneous physical position.

The controller-response model consumes current reported position, next target,
speed, acceleration, elapsed time, channel identity, and calibration identity.
It estimates the realized pose between updates and is fitted and tested using
Phase 2 traces. Runtime chooses a command cadence supported by measured response
and coalesces updates superseded before transmission. Dataset records retain
both requested targets and observed positions.

Safety validation still checks finite and calibrated targets, freshness,
continuity, permitted ranges, and relevant motion limits. The implementation
must distinguish controller setting value zero from an assumed zero physical
speed or acceleration; its meaning comes from the controller specification and
measured response.

## Hierarchical motion model

### Conservative anchor

`AnchorPlanner` maps supported affect coordinates to a morphology-appropriate
coarse pose and expression envelope using reviewed anchors and bounded spline
interpolation. It provides neutral return, a permanent baseline, and the fallback
when learned components are unavailable or invalid.

### Continuous residual dynamics

The primary learned model is a compact neural state-space model conditioned on
filtered affect, intensity, controller state, normalized phase/time, and a
seeded latent style state. It predicts bounded residual target changes around
the anchor rather than unrestricted motor coordinates.

Residual channels cover slow expression drift and medium-timescale asymmetry,
settling, and gaze modulation. Per-channel residual envelopes and regularizers
limit position, target-update rate, realized velocity, acceleration, and jerk.
Rollout and neutral-continuity losses penalize drift and discontinuity.

### Sparse face events

Blinks and other identifiable micro-movements are explicit events. A learned or
statistical policy estimates affect-dependent event timing and parameters. Hard
refractory, coupling, and overlap rules prevent implausible repetition or
conflicting targets. Keeping events explicit supports inspection, ablation, and
meaningful event-rate metrics.

### Head and neck expression

Head motion has two layers:

1. Continuous attitude bias supplies slow affect-dependent yaw, pitch, and tilt.
2. Semantic primitives express nod, shake, tilt, look up/down, and
   return-to-attention.

A learned scheduler decides whether and when a gesture occurs and chooses
bounded amplitude, duration, cycle count, asymmetry, hold, and recovery. A
deterministic primitive generator maps those parameters to sparse targets for
channels 0-2 through the controller-response model. Gesture history and
refractory periods prevent repetitive behavior. Face and eye motion may continue
during a head gesture subject to explicit concurrency rules.

Head motion has its own envelopes, promotion metrics, and conclusions so facial
performance cannot conceal poor neck behavior. The known jankiness of channel 1
at slow speed and the channel 2 software/firmware maximum discrepancy remain
calibration facts to resolve or encode, not details for a model to learn around.

## Dataset design

Each time-aligned episode records:

- affect intent and transition history;
- requested targets, controller speed/acceleration settings, reported positions,
  timestamps, and calibration identity;
- observed robot-face features and observation validity;
- generator/runtime state when the episode is model-generated;
- optional consented human reference and its provenance; and
- participant ratings, uncertainty, and artifact flags.

Collection includes reviewed anchor transitions, continuous affect sweeps, and
long steady-intent sessions designed to expose drift, gaze, blink, and head
gesture behavior. Human references guide affect-equivalent robot behavior rather
than geometric imitation.

Raw identifiable media is opt-in. Each run declares consent, permitted uses,
access, retention, deletion, and whether derived features may be retained.
Participant and session identifiers are pseudonymous in model-facing data.

Train, validation, and test splits are grouped by participant and capture
session. Adjacent windows from an episode never cross splits. Predeclared regions
of affect space are also held out to test interpolation and support detection.
Every retained dataset records source checksums, preprocessing, schema and
calibration identities, split membership, consent constraints, and immutable
provenance. Disposable derived fixtures may use a compact manifest but must
still identify their source and permitted use.

## Training strategy

Training is staged so each source of complexity can be measured:

1. Fit and validate the controller-response model from Phase 2 traces.
2. Establish the anchor-only baseline.
3. Add a procedural seeded-variation baseline.
4. Train bounded residual dynamics using target/pose reconstruction, multistep
   rollout, continuity, neutral return, and realized motion regularization.
5. Train explicit face-event timing and parameters.
6. Train head-gesture scheduling and bounded parameters while retaining the
   deterministic primitive generator.
7. Fine-tune or rank candidates with human preference data only after the
   objective and replay gates pass.

Rating disagreement is preserved as a distribution. Models are not trained to
erase ambiguity or convert cluster annotations into ground truth. Decision-grade
runs record dataset and split identities, configuration, seed, code revision,
dependency lock, model artifact checksum, metrics, and a conclusion. Early
spikes use the compact research record defined above.

## Evaluation

### Automated and replay gates

- Contract, schema, calibration, freshness, and support behavior.
- Deterministic replay and seed-dependent diversity.
- Multistep controller-response error on held-out sessions.
- Continuity across planning windows and intent changes.
- Target cadence and superseded-target coalescing.
- Realized position, velocity, acceleration, jerk, and settling behavior.
- Blink and gesture frequency, refractory violations, conflicts, and collapse.
- Latency, missed deadlines, malformed input, invalid state, and recovery.

### Human evaluation

After offline acceptance, consented raters receive randomized, blinded trials.
Paired trials compare a human reference with the robot response for affect
equivalence. Unpaired trials score naturalness, liveliness, continuity,
intentionality, and discomfort. The report includes distributions, confidence
intervals, inter-rater agreement, presentation order, exclusions, and subgroup
checks justified by the study design.

### Ablations and promotion

Compare anchor-only, procedural variation, learned residuals, learned face
events, and learned head scheduling on identical splits. The state-space model
is promoted only when held-out human ratings improve without worse continuity,
controller-response, or constraint metrics across predeclared seeds.

Face and head results are reported separately. Failure to improve is a valid
research result and leaves the simpler baseline selected.

## Alternative research paths

### Rolling conditional transformer

Evaluate when data shows that long context, interaction among multistage events,
or variable history materially limits the state-space model. Prefer an
offline/bidirectional window unless streaming causality is actually required.
It must preserve horizon-boundary continuity and meet the same latency and
replay gates.

### Conditional diffusion

Evaluate when repeated demonstrations show meaningful, desirable multimodality
not captured by seeded latent dynamics. Compare distributional coverage and
human-rated diversity, not only best-sample quality. Streaming continuity and
latency are explicit admission risks.

### Fully learned neck dynamics

Compare against the semantic primitive system only when sufficient neck gesture
demonstrations exist. Promotion requires better human ratings without increased
constraint failures, reduced controllability, or loss of meaningful gesture
parameters.

### Other admissible candidates

Conditional GRUs, temporal convolutional networks, neural ODE/state-space
variants, mixture-density heads, and retrieval/motion-graph methods may enter as
experiments behind the same interfaces. Each proposal must state the observed
baseline limitation it addresses and a falsifiable promotion criterion.

## Failure behavior

Malformed, non-finite, stale, unsupported, or schema-incompatible affect input
does not update the generator. Invalid model state, missed deadlines,
discontinuous horizons, and validation failures discard the candidate prefix.
The runtime smoothly maintains or returns to the last valid safe anchor or
neutral pose according to configured policy. It does not reuse stale intent as
fresh input, silently change calibration, or switch from simulation to hardware.

Model processes never open serial, camera, or ROS hardware as a side effect of
loading. `SafetySupervisor` and the actuator adapter remain separate processes
or modules with explicit contracts. Hardware execution is outside this design's
automatic workflow.

## Deliverables and research record

The implementation plan derived from this design will cover contracts,
controller-response modeling, dataset manifests, baselines, stateful generation,
face events, neck primitives and scheduling, evaluation, packaging, and
simulation integration. Retained experiments get a versioned config, metrics,
artifact manifest, model card where applicable, and short conclusion; disposable
spikes use the compact record described in the research operating mode.

Durable specifications and implementation plans live under `docs/superpowers/`
and are committed. Temporary visual-companion state lives under `.superpowers/`
and is ignored.
