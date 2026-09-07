# Affect Motion Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish controller-aware affect and target-update contracts plus an immediately usable anchor/procedural face-head generator.

**Architecture:** Build on the reviewed Phase 2 branch. The generator emits sparse semantic target updates, while a separate response model estimates Maestro interpolation; a deterministic anchor and seeded procedural layer provide the first believable-motion baseline.

**Tech Stack:** Python 3.12–3.13, Pydantic 2, NumPy, PyYAML, pytest, ruff, mypy

**Spec:** `docs/superpowers/specs/2026-09-07-streaming-affect-motion-design.md`

## Global Constraints

- Integrate `feature/phase-2-actuator-identification` before creating model files; do not copy its files manually.
- Continuous coordinates are canonical; cluster labels are optional metadata only.
- Outputs are sparse proposals and never import or open hardware adapters.
- Replays are deterministic for identical input, model/config identity, initial state, and seed.
- Use compact records for disposable spikes and full manifests only for retained evidence.

---

### Task 1: Integrate and verify the Phase 2 foundation

**Files:**
- Merge: `feature/phase-2-actuator-identification`
- Verify: `pyproject.toml`, `src/alice/contracts/actuation.py`, `src/alice/hardware/manifest.py`

**Interfaces:**
- Consumes: the existing feature branch.
- Produces: one branch containing perception, actuation contracts, semantic manifest, mock/replay paths, and the approved design docs.

- [ ] **Step 1: Review branch scope and conflicts**

Run: `git log --oneline main..feature/phase-2-actuator-identification && git diff --stat main...feature/phase-2-actuator-identification`

Expected: only Phase 1/2 implementation and documentation changes are listed; unrelated changes stop integration for review.

- [ ] **Step 2: Merge through the finishing-development-branch workflow**

Use the repository's `finishing-a-development-branch` skill. Resolve conflicts by preserving ADR 0003 and the 2026-09-07 plan series while retaining the feature branch's executable package.

- [ ] **Step 3: Verify the merged foundation**

Run: `uv sync && uv run pytest -q && uv run ruff check src tests && uv run mypy src`

Expected: all commands exit 0 and tests do not require a camera or Maestro.

- [ ] **Step 4: Commit conflict resolutions if the merge requires them**

```bash
git add pyproject.toml uv.lock src tests config hardware docs
git commit -m "merge: integrate actuator identification foundation"
```

Skip this commit only when Git already created a clean merge commit.

### Task 2: Affect intent and sparse target-update contracts

**Files:**
- Create: `src/alice/contracts/affect.py`
- Create: `src/alice/contracts/motion.py`
- Test: `tests/contracts/test_affect.py`
- Test: `tests/contracts/test_motion.py`
- Create: `config/affect/affect-vector-v1.yaml`

**Interfaces:**
- Produces: `AffectIntent`, `AffectVectorSchema`, `TargetUpdate`, `TargetUpdateHorizon`, `MotionProposal`.

- [ ] **Step 1: Write failing contract tests**

```python
def test_affect_is_continuous_and_labels_are_optional() -> None:
    intent = AffectIntent.model_validate(valid_intent(vector=(0.2, -0.3, 0.8)))
    assert intent.vector == (0.2, -0.3, 0.8)
    assert intent.cluster_labels == ()

def test_horizon_requires_increasing_offsets() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        TargetUpdateHorizon.model_validate(horizon(offsets=(0.2, 0.1)))
```

- [ ] **Step 2: Verify the tests fail for missing modules**

Run: `uv run pytest tests/contracts/test_affect.py tests/contracts/test_motion.py -v`

Expected: collection fails because `alice.contracts.affect` and `alice.contracts.motion` do not exist.

- [ ] **Step 3: Implement frozen Pydantic contracts**

Use schema IDs `affect-intent/v1`, `target-update-horizon/v1`, and `motion-proposal/v1`. Require ordered finite vector values in `[-1, 1]`, intensity in `[0, 1]`, timestamp/expiry/source identity, strictly increasing offsets, unique semantic actuator targets, seed, model identity, calibration hash, and controller-settings hash. Keep cluster labels optional and non-authoritative.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/contracts/test_affect.py tests/contracts/test_motion.py -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/contracts config/affect tests/contracts
git commit -m "feat: define affect and sparse motion contracts"
```

### Task 3: Intent filtering and affect-space support

**Files:**
- Create: `src/alice/motion/intent_filter.py`
- Create: `config/affect/intent-filter-v1.yaml`
- Test: `tests/motion/test_intent_filter.py`

**Interfaces:**
- Produces: `FilteredIntent`, `SupportStatus`, `IntentFilter.update(intent, previous, now_ns) -> FilteredIntent`.

- [ ] **Step 1: Write failing freshness, smoothing, and support tests**

```python
def test_stale_intent_preserves_last_valid_target() -> None:
    filtered = filter.update(stale_intent(), previous(), now_ns=900)
    assert filtered.support_status == SupportStatus.STALE
    assert filtered.vector == previous().vector

def test_outside_demonstrated_support_requests_fallback() -> None:
    assert filter.update(outlier(), previous(), 10).support_status == SupportStatus.FALLBACK
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/motion/test_intent_filter.py -v`

Expected: missing intent filter module.

- [ ] **Step 3: Implement validation, smoothing, and support distance**

Reject stale/schema-incompatible updates, apply a configurable first-order transition filter using monotonic elapsed time, and estimate support from normalized distance to retained training coordinates. Return `SUPPORTED`, `INTERPOLATED`, `FALLBACK`, or `STALE` with the distance and reason; never clamp an unsupported vector into a supported label.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/motion/test_intent_filter.py -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/motion/intent_filter.py config/affect/intent-filter-v1.yaml tests/motion/test_intent_filter.py
git commit -m "feat: filter and qualify affect intent"
```

### Task 4: Controller-response model and target coalescing

**Files:**
- Create: `src/alice/motion/controller_response.py`
- Create: `src/alice/motion/coalescing.py`
- Test: `tests/motion/test_controller_response.py`
- Test: `tests/motion/test_coalescing.py`
- Create: `config/models/maestro-response-v1.yaml`

**Interfaces:**
- Produces: `ControllerState`, `ControllerResponse.predict(state, update, elapsed_s) -> ControllerState`, `coalesce_updates(updates, transmit_at_s) -> tuple[TargetUpdate, ...]`.

- [ ] **Step 1: Write failing response and coalescing tests**

```python
def test_response_never_overshoots_target() -> None:
    observed = model.predict(state(position=0.0), update(target=0.8), elapsed_s=0.02)
    assert 0.0 <= observed.position <= 0.8

def test_coalescing_keeps_latest_unsent_target() -> None:
    assert coalesce_updates(updates_at(0.01, 0.02, 0.03), transmit_at_s=0.04)[-1].target == 0.03
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/motion/test_controller_response.py tests/motion/test_coalescing.py -v`

Expected: import failure for `alice.motion`.

- [ ] **Step 3: Implement a measured-response abstraction**

Implement a monotone piecewise acceleration/speed-limited predictor whose parameters are loaded per semantic actuator. Treat Maestro setting `0` as a named controller mode resolved by config, never numeric zero motion. Coalescing retains the latest update per actuator before a transmission instant and preserves distinct event timestamps after it.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/motion -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/motion config/models/maestro-response-v1.yaml tests/motion
git commit -m "feat: model controller response and sparse updates"
```

### Task 5: Anchor and seeded procedural baseline

**Files:**
- Create: `src/alice/motion/anchors.py`
- Create: `src/alice/motion/procedural.py`
- Create: `config/models/procedural-motion-v1.yaml`
- Test: `tests/motion/test_anchors.py`
- Test: `tests/motion/test_procedural.py`

**Interfaces:**
- Produces: `AnchorPlanner.plan(intent, state, horizon_s) -> TargetUpdateHorizon`, `ProceduralMotionGenerator.step(intent, state, seed, horizon_s, *, generated_monotonic_ns) -> MotionProposal`.

- [ ] **Step 1: Write failing baseline tests**

```python
def test_same_seed_replays_identically() -> None:
    assert generator.step(
        intent, state, 41, 2.0, generated_monotonic_ns=10_000_000_000
    ) == generator.step(
        intent, state, 41, 2.0, generated_monotonic_ns=10_000_000_000
    )

def test_unsupported_coordinate_returns_anchor_fallback() -> None:
    proposal = generator.step(
        outside_support_intent(),
        state,
        7,
        1.0,
        generated_monotonic_ns=10_000_000_000,
    )
    assert proposal.support_status == "fallback"
    assert proposal.horizon == anchor.plan_neutral(state, 1.0)
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/motion/test_anchors.py tests/motion/test_procedural.py -v`

Expected: tests fail because planners do not exist.

- [ ] **Step 3: Implement the smallest living-motion baseline**

Interpolate only reviewed anchors. Add band-limited seeded drift and explicit blink/gaze timers with configured amplitude and refractory intervals. Emit targets at the configured effective cadence, continue from the accepted state, timestamp proposals with an explicit generation time independent of accepted intent time, and return fallback outside configured affect support. Treat proposal expiry as exclusive and schedule every horizon update strictly before it.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/motion -v && uv run ruff check src tests && uv run mypy src && git diff --check`

```bash
git add src/alice/motion config/models/procedural-motion-v1.yaml tests/motion
git commit -m "feat: add seeded procedural motion baseline"
```
