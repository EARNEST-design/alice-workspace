# ADR 0005: Coordinated streaming motion learning

- Status: direction recorded for the next implementation session; model promotion requires evidence
- Date: 2026-09-07
- Extends: [ADR 0003](0003-streaming-affect-conditioned-motion.md)
- Execution handoff: [Next-session plan](../superpowers/plans/2026-09-07-ml-architecture-next-session.md)

## Context

The streaming work through `d2614b3` establishes motion contracts, a procedural
baseline, a residual GRU, explicit face events, semantic head primitives, and
model packaging. These are useful components, but their composition and empirical
evaluation are the next priorities. Software test coverage is not evidence that
the resulting behavior is believable or affect-equivalent.

The architecture discussion recommended a coordinated probabilistic motion
policy with a calibrated model of the robot's response. The user requested a
written plan to drive the next session. This record captures that direction;
it does not select an untested model as the production policy.

## Decisions

### Preserve the established boundaries

Keep continuous affect, semantic actuator identities, reviewed anchors, explicit
events, deterministic replay, and sparse motion proposals. Perception, behavior
selection, motion generation, validation, and actuator access remain independently
testable. Existing mock/replay and deliberate hardware-enablement rules remain.

### Separate affect from behavioral intent

Affect describes requested expressive qualities. Behavioral context additionally
identifies listening, speaking, agreement, hesitation, an attention target, or an
explicit gesture request. Optional speech/prosody context can be introduced when
the task and data support it. Inferred human affect remains separate from the
expression Alice is requested to produce. Missing optional context must be an
explicit condition that the baseline can handle.

### Share temporal context while retaining separate output heads

Face expression, gaze, blink, and head gestures should share temporal context and
a persistent style variable. Separate output heads retain interpretable events
and independent face/head metrics. A deterministic composer owns actuator
conflicts, event overlap, and recovery from the accepted pose.

The first composer uses the existing procedural and configured components. A
learned context encoder and learned event heads enter only through a comparison
with that working baseline. Merely sharing a timestamp does not constitute
learned coordination.

### Make style and motion state persistent

The current residual GRU is deterministic for fixed inputs, hidden state, and
weights; it does not consume an explicit sampled style input. Model diversity
therefore needs an intentional stochastic mechanism. Preserve style, event
progress, latent state, and random-generator state across chunks. Choose style
at an episode or behavior boundary, with a defined transition policy, rather
than resampling it on every short prefix.

Persist the executed boundary separately from speculative future state. A new
candidate must preserve already committed motion and condition on recent motion
and continuing events. Discarded lookahead must not corrupt accepted state.

### Model the response actually being evaluated

Keep requested motor targets, controller pulse outputs, and camera-derived robot
motion as distinct time-aligned signals with validity and uncertainty. The
hardware contracts already distinguish controller outputs from mechanical
measurements; the ML state and dataset must preserve that distinction.

Begin with interpretable controller dynamics and a small learned correction
fitted to synchronized robot recordings. Training and evaluation must identify
the same response dynamics. A differentiable approximation needs its own
version, fit/validation evidence, and documented relationship to the reference
model. Its predicted positions are not measured physical positions.

### Compare distributions over motion chunks

Retain the GRU and procedural generators as baselines. The first learned
challenger is a small ACT-style conditional latent model over motion chunks,
adapted to Alice's semantic actuator representation and persistent style.
Transformer depth and latent size remain experiment parameters, not claims of
required capacity.

A conditional flow-matching model is a later challenger when repeated
demonstrations expose meaningful diversity or quality that the simpler model
misses. Action-chunking and diffusion results from manipulation motivate
experiments; they do not establish expressive-motion performance on Alice.

### Prioritize the evidence loop

Collect repeated responses to the same condition, transitions, sustained behavior,
and synchronized robot observations. Human references guide affect equivalence
and timing; they do not directly provide Alice motor targets.

Group retained splits by participant and session, keep adjacent windows together,
and hold out declared affect regions. Evaluate long streaming sequences for
continuity, response accuracy, event coordination, responsiveness, collapse, and
diversity. Compare blinded human preferences and preserve disagreement. Report
face and head results separately.

## Alternatives and consequences

| Approach | Intended role | Main tradeoff |
| --- | --- | --- |
| Procedural / GRU with persistent style | Baseline and first integrated runtime | Low cost and inspectable; limited learned coordination |
| Small ACT-style conditional latent chunk model | First learned challenger after the data/evaluation gate | Models alternate coherent sequences; requires demonstrations and latent-use checks |
| Conditional flow-matching chunk model | Later experiment with an explicit admission hypothesis | Flexible distribution modeling; adds sampling, latency, and boundary-conditioning work |

This direction adds a composer, richer conditioning, and response-state clarity
before expanding the model catalog. It leaves existing safety authority intact
and requires a usable baseline even if every learned challenger is rejected.

## Verification and promotion

1. Close or reclassify the seven findings in the next-session plan against the
   actual implementation revision.
2. Demonstrate deterministic uninterrupted and restored streaming runs, including
   intent transitions and missing/stale input.
3. Validate controller-response predictions against independent held-out traces
   before using them as evidence about realized motion.
4. Require comparable dataset/split identities, predeclared metrics and seeds,
   and blinded preference evidence before selecting a learned challenger.

## Research references

- [ACT: Learning Fine-Grained Bimanual Manipulation with Low-Cost Hardware](https://arxiv.org/abs/2304.13705)
- [Diffusion Policy](https://arxiv.org/abs/2303.04137)
- [Flow Matching for Generative Modeling](https://arxiv.org/abs/2210.02747)
- [Real-Time Execution of Action Chunking Flow Policies](https://arxiv.org/abs/2506.07339)
- [EMAGE: coordinated expressive gesture generation](https://arxiv.org/abs/2401.00374)
- [Pololu Maestro documentation: controller pulse output semantics](https://www.pololu.com/docs/0J040/all)
