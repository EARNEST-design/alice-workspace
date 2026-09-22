# Coordinated Streaming Motion: Next-Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use the execution method authorized in the next session; preserve work already in progress.

**Goal:** Establish one correct, continuously running baseline and a concrete data/evaluation path toward coordinated probabilistic motion learning.

**Architecture:** Preserve the existing proposal and actuation boundaries. Repair streaming composition, then introduce shared behavioral context, persistent style, and a response model that distinguishes commands from observations. Compare a small ACT-style chunk model only after the baseline and evidence loop work; admit flow matching through a later measured experiment.

**Tech Stack:** Existing Python 3.12–3.13, Pydantic 2, NumPy, PyTorch, safetensors, PyYAML, pytest, Ruff, and mypy. Keep ML dependencies behind the existing optional extra. No new dependency is needed to begin the review repairs.

**Spec:** [ADR 0005](../../architecture/0005-coordinated-streaming-motion-learning.md), extending [the approved streaming design](../specs/2026-09-07-streaming-affect-motion-design.md).

## Global Constraints

- Never command physical actuators without an explicit, reviewed bring-up procedure and user approval.
- Default hardware adapters to dry-run/mocked operation.
- Never commit credentials, personal participant data, raw biometric data, or proprietary datasets.
- Track data provenance, consent constraints, model versions, evaluation splits, and experiment seeds.
- Keep perception, decision/policy, and actuation behind explicit interfaces.
- Add tests before changing behavior; verify claims with commands and captured results.
- Treat `venetanji/alice-feedback` and other shared repositories as references unless reuse is explicitly approved and license-compatible.
- Model outputs remain sparse proposals; loading a model must not open serial, cameras, or ROS.
- Preserve the distinction between requested targets, controller pulse outputs, and observed robot motion.
- The checked-in priors and synthetic fixtures are software-verification material, not empirical evidence of believable motion.

---

## Where to start

The executable implementation is in:

```text
/home/alice/alice-workspace/.worktrees/streaming-affect-motion
branch: feature/streaming-affect-motion
reviewed commit: d2614b3
```

The main checkout at `/home/alice/alice-workspace` contains this handoff and the
architecture record. Read them there even if the implementation branch has not
yet incorporated these documents. Do not restart implementation in the main
checkout or reimplement the already integrated Phase 1/2 foundation.

At handoff preparation, the implementation worktree had uncommitted changes in
`src/alice/models/package.py` and `tests/models/test_package.py`. These may already
address package findings. Inspect and preserve them. Recheck all paths and git
state at execution time; this record is a snapshot, not an instruction to reset
the worktree to the reviewed commit.

Historical verification at `d2614b3`: **557 tests passed**, Ruff and mypy passed,
and `git diff --check` passed. Two wheel-build tests initially failed inside the
review sandbox and passed when build-cache access was available. These results
do not certify subsequent edits.

## Next-session objective and stopping point

Begin with Tasks 1–5 below. Complete a review repair before moving to its
dependent integration work. If the session ends before all repairs are complete,
leave a precise continuation record with completed tests and the next unchecked
step. Do not spend the session adding model families while baseline correctness
remains unresolved.

After repair, use Task 6 to define and verify one integrated baseline. The later
milestones are a research sequence; they are not a claim that data collection,
model training, and human evaluation fit into one session.

## File ownership and responsibilities

All code paths below are relative to the implementation worktree.

| Files | Responsibility |
| --- | --- |
| `src/alice/motion/state.py`, `streaming.py`, `procedural.py` | Accepted-state continuation, sparse pose accumulation, persistent procedural timing |
| `src/alice/motion/face_events.py` | Explicit facial event state and fallback behavior |
| `src/alice/models/head_scheduler.py`, `src/alice/motion/head_primitives.py` | Gesture scheduling, accepted-pose recovery, consistent history validation |
| `src/alice/models/package.py` | Compatible identities/configurations and valid tensor loading |
| `src/alice/training/residual.py`, `src/alice/motion/controller_response.py` | Explicitly identified training and replay response dynamics |
| `tests/motion/`, `tests/models/`, `tests/training/` | Behavioral regressions for the repairs |
| Proposed `src/alice/motion/composer.py` | One owner of channel composition and in-progress event blending |
| Proposed `tests/motion/test_composed_streaming.py` | Integrated long-run, restart, and transition checks |

Proposed files are scoped to the follow-on integration design. Do not create
empty scaffolding merely to match this table.

### Task 1: Rebaseline and reconcile concurrent work

**Files:** Read `AGENTS.md`, `agents/qa-simulation.md`, `agents/motion-control.md`,
`agents/mlops-evaluation.md`, and the current diffs.

**Interfaces:** Consumes the actual implementation worktree and this handoff;
produces a current revision/status/test record and a finding checklist.

- [ ] Run the following from the implementation worktree:

```bash
git status --short
git log -8 --oneline
git diff -- src/alice/models/package.py tests/models/test_package.py
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/mypy src
git diff --check
```

- [ ] If the environment is absent, use the repository's locked dependency
  workflow (`uv sync --locked`) rather than changing dependency versions.
- [ ] Mark each finding below as reproduced, already fixed with a regression,
  or requiring a changed repair because its implementation has moved.
- [ ] Preserve other work. Do not reset, clean, stash, or stage unrelated edits.

### Task 2: Repair streaming state and sparse boundary handling

**Files:** Modify `src/alice/motion/streaming.py`, `state.py`, and `procedural.py`
as required; extend `tests/motion/test_streaming.py` and
`tests/motion/test_procedural.py`.

**Interfaces:** Preserve `StreamingMotionGenerator.replan(intent, state, now_ns)`
and `CandidatePlan(proposal, boundary_state)`. The boundary state describes all
accepted actuator targets after applying the prefix, not only its last delta.

**Finding A — timers restart:** Each procedural candidate starts new relative
timers. With the existing 0.4-second prefix, initial blink delay is at least
2.5 seconds and gaze delay at least 1.5 seconds. Neither event reaches execution.
The 60-second review replay kept all eyelid and gaze targets at zero.

**Finding B — sparse updates are treated as whole poses:** A horizon containing
an initial complete pose, then a mouth delta at 0.2 seconds and a neck delta at
0.4 seconds, is rejected when the boundary correctly retains both deltas.

- [ ] Add this long-run regression to the existing streaming test module, using
  its `_runtime`, `_state`, and `_intent` helpers. This is a deterministic fixture
  check, not a universal statistical event-rate assertion:

```python
def test_short_prefix_stream_retains_blink_and_gaze_timers() -> None:
    runtime, config = _runtime()
    state = _state(config)
    eye_names = {
        "upper_eyelids", "lower_eyelids",
        "right_eye_horizontal", "left_eye_horizontal",
    }
    moved = set()
    for _ in range(150):
        prefix, state = runtime.replan(
            _intent(accepted_ns=state.monotonic_ns), state, state.monotonic_ns
        )
        moved.update(
            target.actuator_name
            for update in prefix.updates
            for target in update.targets
            if abs(target.normalized_position) > 1e-9
        )
    assert eye_names <= moved
```

- [ ] Add a valid sparse candidate fixture with the exact times and channels in
  Finding B. Assert that the accepted full pose contains mouth `0.1`, neck `0.2`,
  and all unchanged channels. Assert JSON restoration preserves that pose.
- [ ] Run `.venv/bin/python -m pytest tests/motion/test_streaming.py tests/motion/test_procedural.py -q`
  and confirm the new regressions fail for the reported reasons.
- [ ] Implement chronological target accumulation at both candidate construction
  and boundary validation. The essential operation is:

```python
positions = {
    target.actuator_name: target for target in state.last_accepted_target.targets
}
for update in accepted_updates:
    positions.update({target.actuator_name: target for target in update.targets})
boundary_target = TargetUpdate(
    offset_s=0.0,
    targets=tuple(positions[name] for name in sorted(positions)),
)
```

- [ ] Persist procedural event timing and drift phase in validated continuation
  state. Advance accepted state only to the prefix boundary. Do not solve the
  timer bug by reducing refractory periods or extending every accepted prefix.
- [ ] Test uninterrupted versus save/load continuation, a prefix crossing an
  event, an intent transition, and discarded speculative lookahead. Require
  equivalent accepted behavior at the same configured decision instants.
- [ ] Run the focused suite, inspect the diff, and create a focused commit after
  review: `fix: preserve sparse streaming continuation`.

### Task 3: Repair facial fallback and head recovery composition

**Files:** Modify `src/alice/motion/face_events.py`,
`src/alice/models/head_scheduler.py`, and `src/alice/motion/head_primitives.py`;
extend their existing test modules.

**Interfaces:** `FaceEventGenerator.sample` must distinguish future events from
already accepted events. `HeadGestureScheduler.sample`, `record`, and
`HeadPrimitiveGenerator.render` must agree on pose-bound recovery.

**Finding C — fallback still generates facial events:** Empty history, seed 3,
and a 10-second horizon produce new blink/gaze events for both stale and fallback
intent, as well as supported intent.

**Finding D — head drift prevents gestures:** With checked-in defaults and seed
25, a scheduled shake renders from neutral but fails from a pose where yaw,
tilt, and pitch are all `0.01`. Configured zero recovery conflicts with the
primitive's requirement to preserve inactive axes. Scheduler history validation
also needs to accept the repaired recovery representation.

- [ ] Add the following to the existing face-event tests and confirm failure:

```python
@pytest.mark.parametrize("status", [SupportStatus.STALE, SupportStatus.FALLBACK])
def test_fallback_does_not_schedule_new_face_events(status: SupportStatus) -> None:
    generator = _generator()
    state = generator.state_from(_generic_state())
    values = _intent().model_dump()
    values["support_status"] = status
    intent = FilteredIntent.model_validate(values)
    assert generator.sample(intent, state, np.random.default_rng(3), 10.0) == ()
```

- [ ] Add a head integration test that samples the default scheduler with seed
  25 and renders the result from `(neck_rotation=.01, head_tilt=.01,
  face_pitch=.01)`. Keep a neutral control. After repair, record and restore the
  rendered gesture through the scheduler's actual history API.
- [ ] Suppress new face events under stale/fallback input while letting already
  accepted events follow a defined completion/recovery policy. Cancel uncommitted
  future events according to that policy; do not abruptly drop an active blink.
- [ ] Bind ordinary gesture recovery to the accepted head pose. Preserve
  inactive axes and retain an explicit neutral-return behavior for an intentional
  return gesture. If the scheduler needs pose input, introduce it as an explicit
  keyword argument and update composition callers and history checks together.
- [ ] Verify focused tests, then commit the independently reviewable fixes:

```bash
.venv/bin/python -m pytest tests/motion/test_face_events.py tests/motion/test_head_primitives.py tests/models/test_head_scheduler.py -q
```

### Task 4: Verify package compatibility and finite weights

**Files:** Inspect existing edits in `src/alice/models/package.py` and
`tests/models/test_package.py` before modifying either.

**Interfaces:** Preserve `save_package(path, components, metadata)` and
`load_package(path, expected_identities)`. Both must reject incompatible
components and nonfinite weights; failed saves must not publish a final package.

**Finding E — dimensions can disagree:** A validated head configuration with an
extra affect dimension and a corresponding extra policy weight passes save/load
alongside the standard three-dimensional components. Its first normal intent
then fails. A face configuration using unknown actuator identities can also be
saved and subsequently fail its own loader.

**Finding F — NaN tensors pass:** Matching SHA-256 checksums and strict tensor
shape/key loading do not reject NaN weights. A NaN residual-head bias causes
finite inputs to produce nonfinite output.

- [ ] Inspect the new package tests for coverage of ordered dimension equality,
  actuator compatibility, finite tensor values, and pre-publication rejection.
- [ ] Extend any missing regression with normally validated config objects.
  For the invalid weight fixture, use the existing `_valid_inputs` helper and
  create the actual invalid safetensors artifact:

```python
components, metadata = _valid_inputs(tmp_path)
tensors = load_file(components.weights, device="cpu")
tensors["residual_head.bias"].fill_(float("nan"))
save_file(tensors, components.weights)
record = deepcopy(metadata.training_record)
record["artifact"]["weights_sha256"] = hashlib.sha256(
    components.weights.read_bytes()
).hexdigest()
invalid_metadata = MotionModelMetadata(
    identities=metadata.identities,
    training_record=record,
    metrics_reference=metadata.metrics_reference,
    seed_policy=metadata.seed_policy,
)
with pytest.raises(ValueError):
    save_package(tmp_path / "invalid-model", components, invalid_metadata)
assert not (tmp_path / "invalid-model").exists()
```

- [ ] Exercise the load boundary separately with a checksum-consistent invalid
  artifact. A byte-corruption checksum test alone does not cover finiteness.
- [ ] Run `.venv/bin/python -m pytest tests/models/test_package.py -q`.
- [ ] Retain and attribute existing fixes; commit only owned changes after
  reviewing the current diff. Do not recreate already passing regressions.

### Task 5: Resolve the training-response identity mismatch

**Files:** Inspect `src/alice/training/residual.py`,
`src/alice/motion/controller_response.py`,
`config/models/residual-state-space-v1.yaml`,
`config/models/maestro-response-v1.yaml`, and
`tests/training/test_residual_training.py`.

**Interfaces:** The training record must identify the dynamics that produced
rollout and derivative losses. `ControllerResponse.predict` remains the current
reference predictor. A tensor backend must preserve autograd if it is used for
training losses.

**Finding G — different dynamics under one identity:** `_response_step` uses a
global scalar recurrence independent of the configured per-actuator response.
From rest, a fixed target of `0.5`, steps of `0.2` seconds, and response rate `4`
produce position `0.6214` by step 3, followed by oscillation. This differs from
the named monotone controller predictor.

- [ ] Capture the discrepancy with this hardware-free probe; retain the result
  as a regression against accidental identity conflation:

```python
position = torch.zeros(1, 1)
velocity = torch.zeros(1, 1)
for _ in range(3):
    position, velocity = _response_step(
        command=torch.full((1, 1), 0.5),
        position=position,
        velocity=velocity,
        elapsed_s=torch.full((1, 1), 0.2),
        response_rate_per_s=4.0,
    )
assert float(position.item()) > 0.5
```

- [ ] Give this approximation its own explicit identity and record its
  parameters if it is retained for synthetic research. Prevent records from
  presenting its losses as outputs of `maestro-response-v1`.
- [ ] Scope a separate tensor-response implementation against the reference
  config. Cover per-actuator speed/acceleration, reversals, nonzero initial
  velocity, zero-setting modes, time partitioning, and finite gradients. Avoid
  silently replacing a differentiable model with detached NumPy calculations.
- [ ] A learned mechanical correction requires robot traces and held-out
  validation. Without that evidence, retain the unfitted-prior label and do not
  claim that either predictor measures actual physical motion.
- [ ] Update training-record/package validation and their tests together if
  response identity fields change. Run the training and package focused suites.

### Task 6: Qualify one integrated baseline and leave the next design ready

**Files:** Existing motion/model modules; proposed
`src/alice/motion/composer.py` and `tests/motion/test_composed_streaming.py` only
after defining the composition interface. Continue the existing data/evaluation
plan rather than creating duplicate dataset schemas.

**Interfaces:** The next integration design must expose one candidate producer
compatible with `CandidatePlan`. It owns composition of anchor, residual,
facial events, and head primitives and returns a complete accepted boundary.
It must declare a channel-ownership policy and specify which state is measured,
estimated, accepted, and speculative.

- [ ] Write the composer design around existing interfaces. Define how head
  recovery binds to pose, how events persist, how fallback works, and how model
  configuration identities cover the whole composed generator.
- [ ] Define the initial benchmark scenarios before implementation: steady
  supported intent, intent transition during a gesture, stale input during a
  blink, save/restart during motion, and a candidate rejected after lookahead.
- [ ] Require fixed-seed replay equality, state restoration equality, retained
  event phase, valid composed targets, no channel conflicts, and separate face/
  head metrics. Report boundary velocity/acceleration changes as well as position
  changes. For the configured synthetic 60-second fixture, require actual blink
  and gaze events; do not treat all-static output as a passing living baseline.
- [ ] Design the command/observation distinction into the ML state before adding
  learned feedback: requested target, controller pulse output, camera-derived
  observation, timestamps, validity, and uncertainty must remain identifiable.
- [ ] Run the full quality gate after repairs or implementation:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/mypy src
git diff --check
git status --short
```

- [ ] Leave a concise session record containing actual HEAD, owned commits,
  remaining dirty files, finding dispositions, test output, replay config/seed,
  and the next unchecked task. Link any retained artifacts by checksum.

## Follow-on research milestones

These milestones preserve the architectural recommendations. Each larger change
gets its own concrete implementation plan after its admission conditions are met.

### Milestone A: Shared context and persistent style

**Entry:** The composed baseline handles continuation, fallback, and restart.

Define a behavioral-context contract alongside `AffectIntent`: interaction mode,
attention target, optional explicit gesture, and optional speech timing. Define
missing-context behavior. Keep desired robot expression separate from inferred
human affect. Introduce a persistent style variable with explicit sampling,
transition, serialization, and seed ownership. Initially feed configured policies;
add a learned shared encoder and separate heads only through an ablation.

**Exit:** The baseline can run the same affect under different declared behavior
contexts and styles, with reproducible state and no chunk-boundary resampling.

### Milestone B: Synchronized episodes and evaluation

**Entry:** One reproducible baseline and the reviewed recording boundary exist.

Execute [the data/evaluation plan](2026-09-07-affect-motion-data-evaluation.md).
Record repeated responses to the same condition, transitions, sustained runs,
controller outputs, and observed robot motion. Human references supply expressive
and temporal guidance; they are not Alice actuator labels. Retain provenance and
consent constraints, grouped splits, adjacent-window grouping, and declared
affect-space holdouts. Hardware collection is a separate approved activity.

Evaluate long-run continuity, response error, event timing/coordination, collapse,
diversity, and responsiveness. Compare blinded human preferences and report face
and head results separately. Preserve individual ratings and uncertainty.

**Exit:** A dataset manifest, reproducible baseline report, and evaluation harness
exist. Acquisition gaps are named. No learned candidate is promoted from synthetic
fixtures or reconstruction error alone.

### Milestone C: Small ACT-style conditional chunk challenger

**Entry:** Repeated demonstrations and the baseline comparison harness exist.

Use [the alternative-model plan](2026-09-07-alternative-motion-models.md) as the
starting point, amended to test a conditional latent distribution over chunks.
Compare the procedural baseline, a GRU with persistent style, and a small
ACT-style model using the same conditioning, outputs, splits, and response model.
The existing two-layer/width-128 transformer proposal is an initial experiment
configuration, not a required capacity. Check whether changing the latent changes
coherent motion, whether a fixed latent persists across chunks, and whether the
model ignores or collapses its latent representation.

**Exit:** Promote only with held-out evidence of improved motion/preferences and
acceptable continuity/latency. Record a rejection if the simpler baseline wins.

### Milestone D: Conditional flow matching, if admitted

**Entry:** A documented diversity or quality limitation remains after Milestone C.

Add conditional flow matching behind the same candidate/composer interface.
Measure inference cost and diversity; condition on recent motion and freeze the
already committed prefix. Investigate real-time chunking/inpainting when the
measured inference schedule requires it. Use the same splits, rating protocol,
response model, and declared seeds as the simpler challengers.

**Exit:** Retain only a measured improvement. No automatic replacement of the
baseline is implied by using a newer model family.

## Suggested opening instruction for the next session

> Read `docs/superpowers/plans/2026-09-07-ml-architecture-next-session.md` and
> ADR 0005 from `/home/alice/alice-workspace`. Work on the existing
> `feature/streaming-affect-motion` worktree. Reconcile current uncommitted
> packaging fixes, reproduce remaining review findings, and execute the repair
> tasks with regression tests. Prioritize a correct continuous baseline and
> leave the integrated-composer design and data/evaluation work ready to proceed.
> Preserve other work and keep execution hardware-free.
