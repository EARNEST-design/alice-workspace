# ADR 0006: Streaming continuation and response provenance

- Date: 2026-09-09
- Status: implemented repairs; same-revision synthetic qualification recorded separately
- Extends: [streaming motion boundaries](0003-streaming-affect-conditioned-motion.md)

## Context

The September 7 next-session handoff names seven baseline defects. The existing
implementation has since added `ProductionCandidateComposer` in
`src/alice/motion/streaming.py`, package validation, pose-aware head windows, and
a read-only readiness gate. A fresh rebaseline at `b42e273` passes 618 tests,
but green tests do not close missing regressions or qualify expressive behavior.

## Composition and continuation

Retain the existing composer and `CandidatePlan(proposal, boundary_state)`
interface. Do not introduce a second composition owner or an empty `composer.py`.
The streaming acceptor validates the full candidate and publishes only its
configured prefix. Its boundary contains the complete pose obtained by applying
all accepted sparse updates chronologically to the previous accepted pose.

| Channels | Owner and combination policy |
| --- | --- |
| All configured channels | The anchor defines the base; the bounded residual adds variation. |
| Coupled eyelids and horizontal gaze | Explicit facial events add their configured envelope; face policies cannot overlap one another or head channels. |
| Semantic head axes | One accepted head primitive owns the head pose for its duration, preserving captured inactive axes and recovering to its captured pose. |
| Final candidate | The composer checks normalized range and controller feasibility. A rejected candidate's random, latent, and newly sampled event state is discarded. |

An ordinary head gesture captures its initial full head pose when selected. Its
recovery target is that same pose. An intentional `HeadGestureKind.RETURN`
(`"return"`) retains
the separately configured neutral-return semantics. History validation and
rendering must agree with both representations.

Procedural continuation retains absolute event timing, a fixed drift phase, and
its random state. Replanning must not redraw initial waiting periods. Speculative
lookahead is evaluated on a copy; only the accepted-prefix continuation is
published. Checkpoint restoration at the same decision instants must reproduce
the accepted behavior.

In the production composer, stale/fallback intent suppresses new facial and head
events. Events that have
already started complete their established phase and recovery; future events
are canceled. Feasibility fallback may project commands to permit safe stopping,
but must preserve accepted event identity and avoid treating rejected lookahead
as accepted motion. A later supported intent must not resurrect canceled events.
Projection retains the event's identity and absolute phase. Subsequent samples
follow that phase from the current accepted controller estimate, never replaying
the onset. If projection prevents exact envelope tracking, the event still ends
at its recorded time; the feasible anchor recovery then continues from the actual
accepted estimate. Software evidence must disclose such projection/fallback use.
The standalone procedural adapter retains its existing exact-neutral fallback
policy: variation is suppressed while its absolute clock continues. Its transition
tests establish deterministic continuation; active-event completion is a claim
about the explicit-event production composer, not that legacy fallback path.

## Composed model identity

The package manifest binds the anchor configuration, residual weights and
configuration, face policy, head policy, and controller-response configuration.
Qualification additionally records the software revision, which identifies the
composition and continuation implementation. Together these identities cover
the whole tested generator; the residual model ID alone does not.

For cross-version runtime restoration, the next state/package contract must
also bind an explicit composition/continuation algorithm version and validate
it before accepting a restored state. The current `GeneratorState` has no such
field: its package hash alone must not be advertised as proving compatibility
across software revisions. This session's replay equality claim is restricted
to the recorded revision and immutable configurations.

## Signal meanings

The current offline composer stores controller predictions in
`last_reported_pose`; that legacy name does not establish a physical measurement.
Until a separate measured-input interface is implemented, this field is an
estimated continuation pose. The composer has no authority to command actuators.

The next ML-state extension must preserve these distinct records:

| Signal | Required meaning and metadata |
| --- | --- |
| Requested target | Semantic actuator values, request timestamp, proposal/model identity, and acceptance state. |
| Controller pulse output | Controller-originated or explicitly predicted pulse value, timestamp, firmware/calibration identity, and validity; never mechanical ground truth. |
| Camera observation | Observed robot feature/pose, capture timestamp, camera/detector identity, validity, confidence, and uncertainty. |
| Estimated state | Predictor identity/configuration hash, time, uncertainty or an explicit unknown-uncertainty marker, and source signals. |
| Speculative state | Candidate-local latent/RNG/event/response state; never a measurement or accepted boundary. |

Do not relabel predicted motion as measured feedback. Reuse the existing
[data/evaluation plan](../superpowers/plans/2026-09-07-affect-motion-data-evaluation.md)
for synchronized records, provenance, consent, and grouped splits.

## Training response identity

`ControllerResponse.predict` remains the reference predictor. A full configuration
hash identifies its parameters, but does not establish equivalence with a
different numerical implementation. Retain the current differentiable training
approximation only under its own versioned identity and resolved parameters.
Training records and packages must distinguish this loss backend from the runtime
controller reference. Both remain unfitted research priors.

The retained backend is `bounded-euler-surrogate/v1`: clamp desired velocity by
the resolved per-actuator speed, clamp velocity change by acceleration times the
sample's elapsed time, and advance position by the updated velocity times that
elapsed time. Cap displacement at remaining target distance, clamp position to
the normalized range, and reset velocity on reaching the target. Its metadata
records ordered speed/acceleration limits and the source reference-config hash.
This numerical rule differs from exact acceleration integration and is sensitive
to time partitioning. It does not independently implement firmware zero-setting
modes. Changing the rule requires a new backend identity, and old records without
backend provenance are rejected rather than silently relabeled.
Training normalizes inputs to CPU float32; direct tensor steps retain the input
dtype/device and autograd graph. The identified dataset supplies per-sample
elapsed times and initial response states.

A future tensor implementation claiming reference equivalence needs a separate
experiment and parity tests for each actuator's speed/acceleration, reversals,
nonzero initial velocity, speed/acceleration zero modes, time partitioning,
normalized-range limits, and finite gradients away from nondifferentiable branch
boundaries. Detached NumPy calls are not a training backend. A learned mechanical
correction additionally needs synchronized robot traces and held-out validation.

## Qualification contract

Define these scenarios before adding the qualification implementation:

1. Steady supported intent for 60 virtual seconds with the checked-in priors,
   fixed seed(s), 1-second lookahead, and 0.4-second accepted prefixes.
2. An intent transition during an accepted head gesture.
3. Stale input during an accepted blink, plus a future event to cancel.
4. Save/restart during motion at every accepted boundary.
5. A candidate rejected after speculative lookahead, followed by deterministic
   recovery from the unchanged accepted state.

Require fixed-seed accepted replay equality, checkpoint equality, retained active
phase, complete valid targets, and unambiguous channel ownership. The configured
60-second fixture must contain actual accepted blink and gaze events. Residual
eye movement alone does not establish either event. Record face and head metrics
separately, including boundary position, velocity, and acceleration changes.
Record fallback use so an all-static or perpetually rejected stream cannot be
mistaken for a living baseline.

Retain the replay configuration, seeds, software/config identities, metrics,
artifact checksums, and a short conclusion. These are synthetic software checks,
not evidence of believable motion or measured hardware accuracy. Model-family
experiments and hardware collection remain subsequent milestones.
