# Stateful Face and Head Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add learned streaming residual dynamics, explicit facial events, and semantic head gestures behind the foundation contracts.

**Architecture:** A stateful generator replans overlapping horizons and emits validated prefixes. Learned components remain bounded residuals or event schedulers; deterministic planners retain pose continuity and interpretability.

**Tech Stack:** Python 3.12–3.13, PyTorch, safetensors, NumPy, Pydantic 2, pytest

**Spec:** `docs/superpowers/specs/2026-09-07-streaming-affect-motion-design.md`

## Global Constraints

- Complete `2026-09-07-affect-motion-foundations.md` first.
- Never train directly on test splits or import hardware from model modules.
- Identical state, intent, weights, and seed must replay identically.
- Face and head outputs and metrics remain separable.

---

### Task 1: Streaming state and overlapping-horizon runtime

**Files:**
- Create: `src/alice/motion/state.py`
- Create: `src/alice/motion/streaming.py`
- Test: `tests/motion/test_streaming.py`

**Interfaces:**
- Produces: `GeneratorState`, `AcceptedPrefix`, `StreamingMotionGenerator.replan(intent, state, now_ns) -> tuple[AcceptedPrefix, GeneratorState]`.

- [ ] **Step 1: Write failing continuity and replay tests**

```python
def test_replanning_starts_at_last_accepted_state() -> None:
    first, state1 = runtime.replan(intent, initial_state(), 0)
    second, _ = runtime.replan(intent, state1, first.ends_at_ns)
    assert second.updates[0].targets == first.updates[-1].targets

def test_state_serialization_preserves_rng() -> None:
    assert step(load_state(dump_state(state))) == step(state)
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/motion/test_streaming.py -v`

Expected: missing state and streaming modules.

- [ ] **Step 3: Implement stateful prefix acceptance**

Store last target/reported pose, estimated velocity, filtered intent, latent vector, NumPy and Torch RNG states, event history, model/calibration/controller identities, and monotonic time. Reject identity changes and discontinuous candidate starts; emit only `prefix_duration_s` from each horizon.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/motion/test_streaming.py -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/motion/state.py src/alice/motion/streaming.py tests/motion/test_streaming.py
git commit -m "feat: add stateful motion replanning"
```

### Task 2: Bounded neural state-space residual

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/alice/models/residual_state_space.py`
- Create: `src/alice/training/residual.py`
- Create: `config/models/residual-state-space-v1.yaml`
- Test: `tests/models/test_residual_state_space.py`
- Test: `tests/training/test_residual_training.py`

**Interfaces:**
- Produces: `ResidualStateSpace.forward(features, hidden) -> tuple[residuals, hidden]`, `train_residual(config, dataset) -> TrainingResult`.

- [ ] **Step 1: Write failing bound and rollout tests**

```python
def test_residual_is_bounded_per_actuator() -> None:
    residual, _ = model(features, hidden)
    assert torch.all(residual.abs() <= model.residual_envelope + 1e-7)

def test_fixed_seed_training_repeats_on_cpu() -> None:
    assert train_tiny(seed=19).weights_sha256 == train_tiny(seed=19).weights_sha256
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/models/test_residual_state_space.py tests/training/test_residual_training.py -v`

Expected: missing model and trainer modules.

- [ ] **Step 3: Implement the compact model and losses**

Add `torch` and `safetensors` as project dependencies and refresh `uv.lock`. Use a GRU state update with affect, intensity, anchor pose, response state, and elapsed time at every step. Apply `tanh * residual_envelope` per actuator. Train with target reconstruction, multistep rollout, anchor-relative drift, horizon-boundary continuity, and realized velocity/acceleration/jerk terms; save weights with safetensors.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/models/test_residual_state_space.py tests/training/test_residual_training.py -v && uv run ruff check src tests && uv run mypy src`

```bash
git add pyproject.toml uv.lock src/alice/models src/alice/training config/models tests/models tests/training
git commit -m "feat: learn bounded residual motion dynamics"
```

### Task 3: Explicit blink and gaze event model

**Files:**
- Create: `src/alice/motion/face_events.py`
- Create: `config/models/face-events-v1.yaml`
- Test: `tests/motion/test_face_events.py`

**Interfaces:**
- Produces: `FaceEvent`, `FaceEventState`, `FaceEventGenerator.sample(intent, state, rng, horizon_s) -> tuple[FaceEvent, ...]`.

- [ ] **Step 1: Write failing event-rule tests**

```python
def test_blinks_respect_refractory_period() -> None:
    events = sample_many(generator, seconds=60, seed=3)
    assert min_gap(events, kind="blink") >= generator.blink_refractory_s

def test_events_are_seeded_but_not_static() -> None:
    assert sample(seed=4) == sample(seed=4)
    assert sample(seed=4) != sample(seed=5)
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/motion/test_face_events.py -v`

Expected: missing face event module.

- [ ] **Step 3: Implement inspectable stochastic events**

Use seeded hazard rates conditioned on affect and elapsed refractory time. Emit named blink and gaze events with onset, hold, release, amplitude, and coupled actuator identities. Reject conflicts and preserve event history across horizons.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/motion/test_face_events.py -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/motion/face_events.py config/models/face-events-v1.yaml tests/motion/test_face_events.py
git commit -m "feat: add seeded facial micro-events"
```

### Task 4: Semantic head gestures and learned scheduling

**Files:**
- Create: `src/alice/motion/head_primitives.py`
- Create: `src/alice/models/head_scheduler.py`
- Create: `config/models/head-gestures-v1.yaml`
- Test: `tests/motion/test_head_primitives.py`
- Test: `tests/models/test_head_scheduler.py`

**Interfaces:**
- Produces: `HeadGesture`, `HeadPrimitiveGenerator.render(gesture, state) -> TargetUpdateHorizon`, `HeadGestureScheduler.sample(intent, history, rng) -> HeadGesture | None`.

- [ ] **Step 1: Write failing semantic and scheduling tests**

```python
@pytest.mark.parametrize("kind", ["nod", "shake", "tilt", "look_up", "look_down"])
def test_primitive_returns_continuously(kind: str) -> None:
    horizon = primitives.render(gesture(kind), state())
    assert horizon.updates[0].targets == state().targets
    assert horizon.updates[-1].targets == gesture(kind).recovery_targets

def test_scheduler_respects_gesture_refractory() -> None:
    assert scheduler.sample(intent, recent_same_gesture(), rng) is None
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/motion/test_head_primitives.py tests/models/test_head_scheduler.py -v`

Expected: missing primitive and scheduler modules.

- [ ] **Step 3: Implement primitives and bounded scheduler**

Map yaw/rotation to channel 0, tilt to channel 1, and face up/down to channel 2 through semantic manifest names rather than numeric channels. Render minimum-jerk envelopes from amplitude, duration, cycles, asymmetry, hold, and recovery. Begin with configured hazard/parameter distributions; add a small learned scheduler only when labeled gesture episodes exist.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/motion/test_head_primitives.py tests/models/test_head_scheduler.py -v && uv run ruff check src tests && uv run mypy src && git diff --check`

```bash
git add src/alice/motion/head_primitives.py src/alice/models/head_scheduler.py config/models/head-gestures-v1.yaml tests
git commit -m "feat: add semantic affective head gestures"
```

### Task 5: Integrity-checked model package

**Files:**
- Create: `src/alice/models/package.py`
- Create: `models/model-cards/streaming-affect-motion-v1.md`
- Test: `tests/models/test_package.py`

**Interfaces:**
- Produces: `MotionModelPackage`, `save_package(path, components, metadata)`, `load_package(path, expected_identities) -> LoadedMotionModel`.

- [ ] **Step 1: Write failing integrity and side-effect tests**

```python
def test_modified_weights_are_rejected(tmp_path: Path) -> None:
    package = write_valid_package(tmp_path)
    package.weights.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        load_package(package.path, identities())

def test_loading_does_not_construct_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("serial.Serial", fail_if_called)
    load_package(valid_package(), identities())
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/models/test_package.py -v`

Expected: missing package module.

- [ ] **Step 3: Implement the package boundary**

Store safetensors weights, resolved configs, affect/motion/calibration/controller identities, training record, metrics reference, seed policy, and SHA-256 checksums. Reject missing or mismatched identities before model construction. Package no training data, participant media, credentials, camera code, ROS code, or serial adapter.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/models/test_package.py -v && uv run ruff check src tests && uv run mypy src && git diff --check`

```bash
git add src/alice/models/package.py models/model-cards/streaming-affect-motion-v1.md tests/models/test_package.py
git commit -m "feat: package streaming motion models safely"
```
