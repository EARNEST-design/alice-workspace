# Alternative Motion Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate richer motion models without displacing the simpler streaming system unless measured limitations and held-out results justify them.

**Architecture:** Every candidate consumes the established affect/state tensors and emits the same bounded residual or semantic gesture contract. Candidate admission begins with a one-page hypothesis and ends with the shared comparison function.

**Tech Stack:** Python 3.12–3.13, PyTorch, safetensors, NumPy, pytest

**Spec:** `docs/superpowers/specs/2026-09-07-streaming-affect-motion-design.md`

## Global Constraints

- Start only after Plans 1–3 produce a selected baseline and a named measured limitation.
- Use the identical dataset/splits, controller-response model, metrics, and rating protocol.
- Stochastic candidates must expose seeds and return bounded outputs.
- A rejected candidate is recorded and removed from the runtime package.

---

### Task 1: Candidate admission record and shared adapter

**Files:**
- Create: `src/alice/models/candidate.py`
- Create: `docs/experiments/templates/candidate-admission.md`
- Test: `tests/models/test_candidate.py`

**Interfaces:**
- Produces: `MotionCandidate.predict(features, state, rng) -> CandidateOutput`, `CandidateAdmission`.

- [ ] **Step 1: Write the failing interface test**

```python
def test_candidate_output_matches_residual_contract() -> None:
    output = FakeCandidate().predict(features(), state(), rng(5))
    assert output.residuals.shape == (features().steps, features().actuators)
    assert output.seed == 5
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/models/test_candidate.py -v`

Expected: missing candidate module.

- [ ] **Step 3: Implement the protocol and admission schema**

Require measured limitation, hypothesis, primary metric, non-inferiority metrics, dataset/split IDs, seed list, compute budget, and stop rule. Validate output shape, finiteness, residual envelope, and state identity in the adapter.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/models/test_candidate.py -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/models/candidate.py docs/experiments/templates/candidate-admission.md tests/models/test_candidate.py
git commit -m "feat: gate alternative motion candidates"
```

### Task 2: Rolling transformer candidate

**Files:**
- Create: `src/alice/models/rolling_transformer.py`
- Create: `config/models/rolling-transformer-v1.yaml`
- Test: `tests/models/test_rolling_transformer.py`

**Interfaces:**
- Produces: `RollingTransformerCandidate.predict(features, state, rng) -> CandidateOutput`.

- [ ] **Step 1: Write failing padding, continuity, and serialization tests**

```python
def test_padding_does_not_change_valid_steps() -> None:
    assert_close(model(short, mask), model(padded, padded_mask)[:, : short.steps])

def test_round_trip_preserves_prediction() -> None:
    assert_close(model(features), load(save(model))(features))
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/models/test_rolling_transformer.py -v`

Expected: missing transformer module.

- [ ] **Step 3: Implement the smallest admitted transformer**

Use two layers, width 128, four heads, feed-forward width 256, configurable dropout, affect/state tokens, relative time encoding, padding masks, and the shared bounded residual head. Use causal masking only when the admission hypothesis specifically concerns unavailable future context.

- [ ] **Step 4: Compare and commit the experiment**

Run: `uv run pytest tests/models/test_rolling_transformer.py -v && uv run alice-compare-motion --candidate rolling-transformer-v1`

```bash
git add src/alice/models/rolling_transformer.py config/models/rolling-transformer-v1.yaml tests/models/test_rolling_transformer.py models/experiments
git commit -m "experiment: evaluate rolling motion transformer"
```

### Task 3: Conditional diffusion and learned-neck candidates

**Files:**
- Create: `src/alice/models/residual_diffusion.py`
- Create: `src/alice/models/learned_neck.py`
- Create: `config/models/residual-diffusion-v1.yaml`
- Create: `config/models/learned-neck-v1.yaml`
- Test: `tests/models/test_residual_diffusion.py`
- Test: `tests/models/test_learned_neck.py`

**Interfaces:**
- Produces: `ResidualDiffusionCandidate.predict(...) -> CandidateOutput`, `LearnedNeckCandidate.predict(...) -> CandidateOutput`.

- [ ] **Step 1: Write failing stochastic-bound and semantic-comparison tests**

```python
def test_diffusion_is_seeded_and_bounded() -> None:
    assert sample(seed=8) == sample(seed=8)
    assert np.max(np.abs(sample(seed=8).residuals)) <= envelope

def test_learned_neck_reports_primitive_equivalent_metrics() -> None:
    assert set(evaluate_neck(candidate).keys()) >= {"return_error", "gesture_rate", "refractory_violations"}
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/models/test_residual_diffusion.py tests/models/test_learned_neck.py -v`

Expected: missing candidate modules.

- [ ] **Step 3: Implement only an admitted candidate**

For a diffusion admission, use conditional one-dimensional residual diffusion with fixed inference steps and the shared envelope projection. For a learned-neck admission, predict bounded yaw/tilt/pitch residuals plus gesture-presence logits while retaining recovery anchoring. If neither admission record validates, record both as `not_run` and do not create runtime model artifacts.

- [ ] **Step 4: Compare, conclude, and commit**

Run: `uv run pytest tests/models -v && uv run alice-compare-motion --candidate residual-diffusion-v1 --candidate learned-neck-v1 && git diff --check`

```bash
git add src/alice/models config/models tests/models models/experiments
git commit -m "experiment: evaluate alternative motion generators"
```
