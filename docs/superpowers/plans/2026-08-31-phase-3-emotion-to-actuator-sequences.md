# Phase 3 Emotion-to-Actuator Sequences Implementation Plan

> **Superseded on 2026-09-07** by
> `docs/superpowers/plans/2026-09-07-streaming-affect-motion-roadmap.md` and its
> linked plan series. Retained as design history; do not execute this checklist.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Train and evaluate models that convert a versioned emotion intent into safe actuator-trajectory proposals, beginning with auditable baselines and admitting a transformer only when it beats them on held-out sessions.

**Architecture:** Emotion intents and actuator trajectories are hardware-independent contracts. Dataset building consumes reviewed Phase 2 artifacts and expression demonstrations, training runs offline with session-grouped splits, and inference returns bounded trajectory proposals that must still pass through the Phase 2 safety supervisor.

**Tech Stack:** Python 3.12, PyTorch, NumPy, Pydantic 2, safetensors, pytest

**Spec:** `docs/architecture/0002-emotion-to-expression-learning-pipeline.md`

## Global Constraints

- `EmotionIntent` represents desired robot behavior, not inferred human ground truth.
- Model output never opens or commands hardware and never bypasses `SafetySupervisor`.
- Dataset provenance, consent constraints, sessions, splits, seeds, code revision, model version, and artifact checksums are mandatory.
- Training, validation, and test splits are grouped by capture session.
- The transformer candidate must beat keyframe and simple learned baselines under predeclared metrics and latency limits.
- Hardware demonstration of a model requires its own reviewed procedure and explicit approval after offline acceptance.

---

### Task 1: Emotion-intent and trajectory contracts

**Files:**
- Create: `src/alice/contracts/emotion.py`
- Create: `src/alice/contracts/trajectory.py`
- Create: `tests/contracts/test_emotion.py`
- Create: `tests/contracts/test_trajectory.py`
- Create: `docs/architecture/emotion-vector-v1.md`

**Interfaces:**
- Produces: `EmotionIntent`, `EmotionVectorV1`, `TrajectoryPoint`, `ActuatorTrajectory`, and `validate_trajectory(trajectory, manifest)`.

- [ ] **Step 1: Define and test a versioned first emotion vector**

Specify dimensions `valence`, `arousal`, and `dominance`, each bounded to `[-1, 1]`, plus intensity `[0, 1]`, onset/hold/release durations, timestamp, validity duration, and source identity. Tests reject missing dimensions, ambiguous ordering, non-finite values, negative durations, stale intents, and intensity outside bounds.

- [ ] **Step 2: Define and test trajectory invariants**

Require strictly increasing relative timestamps, exact semantic actuator names, finite normalized targets, schema/model/calibration identities, and at least two points. Validate names and bounds against `HardwareManifest` without importing an actuator adapter.

- [ ] **Step 3: Run tests and verify they fail**

Run: `uv run pytest tests/contracts/test_emotion.py tests/contracts/test_trajectory.py -v`

Expected: FAIL because the contracts do not exist.

- [ ] **Step 4: Implement contracts and rationale**

Document why version 1 uses valence/arousal/dominance as controllable expressive coordinates, how upstream sources map into them, and that a future schema may add discrete/style dimensions without mutating v1.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/contracts -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/contracts tests/contracts docs/architecture/emotion-vector-v1.md
git commit -m "feat: define emotion and actuator trajectory contracts"
```

---

### Task 2: Expression demonstrations and leakage-safe dataset builder

**Files:**
- Create: `src/alice/models/__init__.py`
- Create: `src/alice/models/dataset.py`
- Create: `config/datasets/emotion-trajectories-v1.example.yaml`
- Create: `tests/models/test_dataset.py`
- Create: `models/datasets/README.md`

**Interfaces:**
- Produces: `TrajectoryExample`, `DatasetManifest`, `build_dataset(config, run_manifests)`, and `grouped_split(examples, seed)`.

- [ ] **Step 1: Write failing provenance and split tests**

Construct examples from three sessions and assert no session appears in more than one split, identical seed `20260831` reproduces membership, missing consent/provenance fields fail validation, actuator and emotion schema hashes match, and adjacent windows from one trajectory remain in one split.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/models/test_dataset.py -v`

Expected: FAIL because the dataset builder does not exist.

- [ ] **Step 3: Implement immutable dataset manifests**

Reference source artifacts by SHA-256 rather than copying them into Git. Record permitted uses, retention constraints, capture sessions, split membership, preprocessing, interpolation rate, calibration identity, and feature/target schema identities.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/models/test_dataset.py -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/models config/datasets tests/models models/datasets
git commit -m "feat: build provenance-aware trajectory datasets"
```

---

### Task 3: Keyframe trajectory baseline

**Files:**
- Create: `src/alice/models/keyframe_baseline.py`
- Create: `config/models/keyframe-baseline-v1.yaml`
- Create: `tests/models/test_keyframe_baseline.py`
- Create: `models/model-cards/keyframe-baseline-v1.md`

**Interfaces:**
- Consumes: `EmotionIntent` and `HardwareManifest`.
- Produces: `KeyframeBaseline.predict(intent) -> ActuatorTrajectory`.

- [ ] **Step 1: Write failing interpolation tests**

Assert neutral intent returns the reviewed Home pose, intensity zero produces no displacement, mirrored positive/negative valence interpolates between reviewed expression anchors, timestamps match onset/hold/release durations, and all targets remain within conservative normalized bounds.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/models/test_keyframe_baseline.py -v`

Expected: FAIL because the baseline does not exist.

- [ ] **Step 3: Implement bounded cubic interpolation**

Use explicitly configured semantic expression anchors, zero endpoint velocity, fixed output sample rate, and no hardware imports. Do not invent anchors for uncalibrated emotion regions; return a structured unsupported-intent error.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/models/test_keyframe_baseline.py -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/models/keyframe_baseline.py config/models tests/models models/model-cards
git commit -m "feat: add auditable keyframe trajectory baseline"
```

---

### Task 4: Simple learned temporal baseline and experiment framework

**Files:**
- Create: `src/alice/models/temporal_mlp.py`
- Create: `src/alice/training/__init__.py`
- Create: `src/alice/training/train.py`
- Create: `src/alice/training/evaluate.py`
- Create: `config/models/temporal-mlp-v1.yaml`
- Create: `tests/models/test_temporal_mlp.py`
- Create: `tests/training/test_reproducibility.py`
- Create: `models/experiments/README.md`

**Interfaces:**
- Produces: `TemporalMLP`, `TrainingManifest`, `train(config, dataset)`, and `evaluate(model, split) -> ModelMetrics`.

- [ ] **Step 1: Write failing shape, bound, and reproducibility tests**

Assert fixed-seed CPU training produces identical weights on a tiny fixture, output shape is `[batch, time, actuators]`, final `tanh` bounds targets, model metadata contains all schema/calibration identities, and training refuses a test split as input.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/models/test_temporal_mlp.py tests/training/test_reproducibility.py -v`

Expected: FAIL because training modules do not exist.

- [ ] **Step 3: Implement the smallest learned baseline**

Condition a time-indexed MLP on emotion vector, intensity, normalized time, and phase indicators. Record seed, code revision, dependency-lock hash, dataset/split hashes, optimizer settings, epoch metrics, best-checkpoint criterion, and safetensors artifact checksum.

- [ ] **Step 4: Implement offline metrics**

Report trajectory MAE, maximum target error, velocity/acceleration violations before supervision, smoothness, inference latency distribution, and—when replay observations exist—weighted blendshape error. Keep metric weights in versioned config.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/models tests/training -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/models src/alice/training config/models tests/models tests/training models/experiments
git commit -m "feat: add reproducible temporal model baseline"
```

---

### Task 5: Transformer candidate behind the same interface

**Files:**
- Create: `src/alice/models/transformer.py`
- Create: `config/models/transformer-v1.yaml`
- Create: `tests/models/test_transformer.py`
- Create: `models/model-cards/transformer-v1.md`

**Interfaces:**
- Consumes and produces the same tensors and `ActuatorTrajectory` contract as `TemporalMLP`.
- Produces: `EmotionTrajectoryTransformer.predict(intent, duration) -> ActuatorTrajectory`.

- [ ] **Step 1: Add a transformer-admission test**

Test architecture behavior on synthetic data: causal mask prevents later desired points from influencing earlier outputs, padding masks exclude padded steps, output is bounded, variable duration produces the requested number of points, and serialization restores identical inference.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/models/test_transformer.py -v`

Expected: FAIL because the transformer candidate does not exist.

- [ ] **Step 3: Implement a compact conditional encoder**

Use learned time embeddings, projected emotion/intensity conditioning, two encoder layers, four attention heads, model width 128, feed-forward width 256, dropout from config, and a final `tanh` actuator head. Keep dimensions configurable but record exact resolved values in the model artifact.

- [ ] **Step 4: Define the evidence gate**

The candidate is not selected unless held-out session results improve the predeclared primary quality metric over both baselines, introduce zero additional post-supervisor constraint violations, meet the configured p95 inference latency, and reproduce across three seeds. Record a rejected candidate as a valid experiment result.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/models/test_transformer.py -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/models/transformer.py config/models/transformer-v1.yaml tests/models/test_transformer.py models/model-cards/transformer-v1.md
git commit -m "feat: add gated emotion trajectory transformer candidate"
```

---

### Task 6: Model comparison and deployment package

**Files:**
- Create: `src/alice/training/compare.py`
- Create: `src/alice/models/package.py`
- Create: `tests/training/test_compare.py`
- Create: `tests/models/test_package.py`
- Create: `models/model-cards/emotion-trajectory-selected.md`
- Create: `docs/experiments/templates/model-comparison-conclusion.md`

**Interfaces:**
- Produces: `compare_candidates(experiment_manifests) -> SelectionResult` and `load_model_package(path, expected_schemas) -> TrajectoryModel`.

- [ ] **Step 1: Write failing selection and package-integrity tests**

Assert selection refuses incomparable dataset/split hashes, applies quality/constraint/latency/reproducibility gates, records every rejected reason, verifies safetensors and config checksums, and refuses schema or calibration mismatch at load time.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/training/test_compare.py tests/models/test_package.py -v`

Expected: FAIL because comparison and packaging do not exist.

- [ ] **Step 3: Implement deterministic comparison and packaging**

Package only weights, resolved model config, contract identities, training manifest, metrics, model card, and checksums. Do not package training data or credentials. Loading a package performs no camera, ROS, or serial initialization.

- [ ] **Step 4: Run complete Phase 3 verification**

Run: `uv run pytest -v && uv run ruff check src tests && uv run mypy src && git diff --check`

Expected: all commands exit 0 and hardware adapters remain unopened.

- [ ] **Step 5: Commit comparison and packaging**

```bash
git add src/alice/training src/alice/models tests/training tests/models models/model-cards docs/experiments/templates
git commit -m "feat: select and package emotion trajectory models"
```

---

### Task 7: Run and conclude the offline model experiment

**Files:**
- Create: `config/experiments/emotion-trajectory-model-comparison.yaml`
- Create: `docs/experiments/emotion-trajectory-model-comparison.md`
- External artifacts: `artifacts/emotion-trajectory-model-comparison/`

**Interfaces:**
- Produces: keyframe, temporal-MLP, and transformer results on identical splits plus a selected model or explicit no-selection result.

- [ ] **Step 1: Freeze dataset, split, metrics, and acceptance gates**

Record immutable hashes before training. Confirm permitted use and retention constraints cover model training and evaluation.

- [ ] **Step 2: Run all candidates for seeds `20260831`, `20260901`, and `20260902`**

Use the same held-out session split and resolved evaluation configuration for every learned candidate. Record failed runs rather than silently retrying with changed settings.

- [ ] **Step 3: Compare candidates and write the conclusion**

Report quality, latency, smoothness, constraint behavior, repeatability, artifacts, limitations, and selection reasons. Select no model if none satisfies every gate.

- [ ] **Step 4: Commit configs, manifests, model cards, and conclusion**

```bash
git add config/experiments/emotion-trajectory-model-comparison.yaml docs/experiments/emotion-trajectory-model-comparison.md models/model-cards
git commit -m "exp: compare emotion trajectory model architectures"
```

Do not run the selected model on hardware. Hardware evaluation is a new safety-reviewed task with a new procedure and explicit approval.
