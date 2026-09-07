# Affect Motion Data and Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create leakage-safe trajectory datasets and determine whether generated motion is controller-faithful, diverse, humanlike, and affect-equivalent.

**Architecture:** Immutable retained-dataset manifests reference episodes and consent metadata without committing media. Automated replay checks precede a lightweight blinded lab-rating workflow; one comparison report promotes or rejects learned components.

**Tech Stack:** Python 3.12–3.13, Pydantic 2, NumPy, pandas, SciPy, PyTorch, PyYAML, pytest

**Spec:** `docs/superpowers/specs/2026-09-07-streaming-affect-motion-design.md`

## Global Constraints

- Split by participant and capture session; adjacent windows never cross splits.
- Human references are affect-equivalent targets, not geometric ground truth.
- Identifiable media is opt-in and external to Git with explicit permitted use and retention.
- Informal feedback guides iteration but cannot be reported as a blinded-study result.

---

### Task 1: Episode and dataset manifests

**Files:**
- Create: `src/alice/data/episodes.py`
- Create: `src/alice/data/splits.py`
- Create: `config/datasets/affect-motion-v1.example.yaml`
- Test: `tests/data/test_episodes.py`
- Test: `tests/data/test_splits.py`

**Interfaces:**
- Produces: `MotionEpisode`, `ConsentConstraints`, `DatasetManifest`, `grouped_affect_split(episodes, seed) -> DatasetSplit`.

- [ ] **Step 1: Write failing provenance and leakage tests**

```python
def test_participant_and_session_do_not_leak() -> None:
    split = grouped_affect_split(episodes(), seed=20260907)
    assert disjoint_groups(split, keys=("participant_id", "session_id"))

def test_identifiable_media_requires_retention() -> None:
    with pytest.raises(ValueError, match="retention"):
        MotionEpisode.model_validate(identifiable_episode(retention=None))
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/data/test_episodes.py tests/data/test_splits.py -v`

Expected: missing data modules.

- [ ] **Step 3: Implement manifests and deterministic grouping**

Record affect history, requested targets, controller settings, reported positions, observations, optional pseudonymous participant/reference IDs, permissions, source checksums, calibration/schema IDs, and ratings. Assign whole participant/session groups first, then designate configured affect-space cells as interpolation holdouts.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/data -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/data config/datasets tests/data
git commit -m "feat: build leakage-safe affect motion datasets"
```

### Task 2: Replay and sequence-quality metrics

**Files:**
- Create: `src/alice/evaluation/motion_metrics.py`
- Create: `src/alice/evaluation/diversity.py`
- Create: `tests/evaluation/test_motion_metrics.py`
- Create: `tests/evaluation/test_diversity.py`

**Interfaces:**
- Produces: `MotionMetrics`, `evaluate_replay(reference, proposal, response_model) -> MotionMetrics`, `seed_diversity(proposals) -> DiversityMetrics`.

- [ ] **Step 1: Write failing metric tests**

```python
def test_boundary_jump_is_detected() -> None:
    metrics = evaluate_replay(reference(), proposal_with_jump(0.2), response_model())
    assert metrics.max_boundary_jump == pytest.approx(0.2)

def test_static_and_jittery_generators_both_fail() -> None:
    assert seed_diversity(static_runs()).collapse_score == 1.0
    assert seed_diversity(jitter_runs()).high_frequency_energy > 0.5
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/evaluation/test_motion_metrics.py tests/evaluation/test_diversity.py -v`

Expected: missing evaluation modules.

- [ ] **Step 3: Implement named metrics**

Measure response-model position error, cadence, coalescing, horizon jumps, velocity/acceleration/jerk, settling, event rates, refractory conflicts, within-seed replay, between-seed diversity, spectral high-frequency energy, and static/repetitive collapse. Report face and head metrics separately and together.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/evaluation -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/evaluation tests/evaluation
git commit -m "feat: evaluate motion continuity and diversity"
```

### Task 3: Lightweight blinded lab-rating workflow

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/alice/evaluation/rating_protocol.py`
- Create: `src/alice/evaluation/rating_analysis.py`
- Create: `config/evaluation/lab-rating-v1.yaml`
- Create: `docs/experiments/templates/lab-rating-brief.md`
- Test: `tests/evaluation/test_rating_protocol.py`
- Test: `tests/evaluation/test_rating_analysis.py`

**Interfaces:**
- Produces: `RatingTrial`, `RatingResponse`, `randomize_trials(trials, rater_id, seed)`, `analyze_ratings(responses) -> RatingSummary`.

- [ ] **Step 1: Write failing blinding and analysis tests**

```python
def test_randomized_trial_hides_model_identity() -> None:
    shown = randomize_trials(trials(), "rater-4", seed=12)
    assert all(trial.public_model_label in {"A", "B", "C"} for trial in shown)

def test_analysis_preserves_disagreement() -> None:
    summary = analyze_ratings(opposed_ratings())
    assert summary.n == 2
    assert summary.distribution[1] == summary.distribution[5] == 0.5
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/evaluation/test_rating_protocol.py tests/evaluation/test_rating_analysis.py -v`

Expected: missing rating modules.

- [ ] **Step 3: Implement export/import workflow**

Add `pandas` and `scipy` as project dependencies and refresh `uv.lock`. Generate randomized JSON/CSV trials for paired affect-equivalence ratings and unpaired naturalness, liveliness, continuity, intentionality, and discomfort ratings. Store pseudonymous rater IDs, presentation order, exclusions, and raw ordinal responses; compute distributions, bootstrap confidence intervals, and Krippendorff alpha without exposing model identity to raters.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/evaluation/test_rating_protocol.py tests/evaluation/test_rating_analysis.py -v && uv run ruff check src tests && uv run mypy src`

```bash
git add pyproject.toml uv.lock src/alice/evaluation config/evaluation docs/experiments/templates tests/evaluation
git commit -m "feat: add blinded motion rating workflow"
```

### Task 4: Ablation, selection, and concise experiment records

**Files:**
- Modify: `pyproject.toml`
- Create: `src/alice/evaluation/compare.py`
- Create: `src/alice/evaluation/cli.py`
- Create: `src/alice/experiments/research_record.py`
- Create: `docs/experiments/templates/motion-comparison.md`
- Test: `tests/evaluation/test_compare.py`
- Test: `tests/experiments/test_research_record.py`

**Interfaces:**
- Produces: `compare_candidates(results) -> SelectionDecision`, `alice-compare-motion` CLI, `SpikeRecord`, `DecisionRecord`.

- [ ] **Step 1: Write failing comparison tests**

```python
def test_better_ratings_cannot_hide_worse_continuity() -> None:
    decision = compare_candidates([baseline(), high_rating_with_jumps()])
    assert decision.selected_id == baseline().model_id

def test_spike_record_stays_small() -> None:
    record = SpikeRecord(config_ref="cfg", data_ref="fixture", seed=2, disposition="discard")
    assert "model_card" not in record.model_dump()
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/evaluation/test_compare.py tests/experiments/test_research_record.py -v`

Expected: missing comparison and research-record modules.

- [ ] **Step 3: Implement predeclared selection gates**

Require identical dataset/split IDs, at least three seeds for learned candidates, non-inferior continuity/constraint metrics, and improved held-out human ratings before promotion. Produce explicit rejection reasons. Keep `SpikeRecord` to config/data/seed/disposition/note; require artifacts and metrics only in `DecisionRecord`. Add `alice-compare-motion = "alice.evaluation.cli:main"` to `[project.scripts]`; its repeated `--candidate` options load experiment manifests and print a JSON `SelectionDecision`.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest -q && uv run ruff check src tests && uv run mypy src && git diff --check`

```bash
git add pyproject.toml src/alice/evaluation src/alice/experiments/research_record.py docs/experiments/templates tests
git commit -m "feat: compare affect motion candidates"
```
