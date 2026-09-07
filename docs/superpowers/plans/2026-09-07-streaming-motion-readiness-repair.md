# Streaming Motion Hardware-Readiness Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the integrated affect-conditioned streaming generator survive indefinite prefix replanning and provide a reviewed, opt-in dry-run readiness check for the connected Alice camera and Pololu controller.

**Architecture:** Fix feasibility at the moving-command/controller boundary rather than weakening the controller solver, preserve each head gesture's complete original pose through recovery, and bind training records to the exact controller-response configuration. Add a read-only readiness entry point that validates stable device identities, package/config compatibility, and a deterministic mock replay without opening the serial command port or issuing actuator targets.

**Tech Stack:** Python 3.12–3.13, PyTorch, NumPy, Pydantic 2, pyserial, OpenCV/V4L2 discovery, pytest

**Spec:** `docs/superpowers/specs/2026-09-07-streaming-affect-motion-design.md`

## Global Constraints

- The connected Pololu and cameras remain uncommanded throughout implementation and verification.
- Ordinary imports/tests remain hardware-free; hardware discovery is explicit and read-only.
- The production composer must deterministically replay from serialized `GeneratorState` for more than one horizon.
- A rejected learned/event candidate must produce a controller-feasible anchor fallback, not an exception or partial state advance.
- Hardware-enabled execution remains a separate explicit command after operator approval; this plan ends at readiness for that test.
- Preserve exact hardware, calibration, controller-response, model, seed, and configuration identities in readiness evidence.

---

### Task 1: Feasible repeated-prefix production composition

**Files:**
- Modify: `src/alice/motion/streaming.py`
- Modify: `src/alice/motion/controller_response.py` only if a reusable non-mutating feasibility query is required
- Test: `tests/models/test_package.py`
- Test: `tests/motion/test_streaming.py`

**Interfaces:**
- Consumes: `ProductionCandidateComposer.plan(...)`, `ControllerResponse.predict(...)`, serialized `GeneratorState`.
- Produces: deterministic multi-prefix planning and an explicit controller-feasible anchor fallback when an event/residual target cannot be realized.

- [ ] **Step 1: Add the exact failing multi-prefix regression**

Extend `test_loaded_composer_replans_deterministically_with_complete_state` to run at least 25 successive 0.4-second prefixes from both original and serialized state with the checked-in seed/configs. Assert equality at every prefix, finite normalized targets, monotonic time, and no controller exception. Add a targeted moving-blink regression using the recorded upper-eyelid position `-0.4664151512`, velocity `-0.3869662820`, and requested target `-0.4718372893`; assert that planning returns a valid fallback proposal and does not partially advance stochastic state.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/models/test_package.py::test_loaded_composer_replans_deterministically_with_complete_state tests/motion/test_streaming.py -v`

Expected: FAIL with `state velocity exceeds available stopping distance` on repeated planning.

- [ ] **Step 3: Implement feasibility-aware composition and fallback**

Before advancing a controller state, validate that a moving command leaves enough stopping distance. If residual/event ownership creates an infeasible command, discard that candidate's stochastic/latent/event changes and compose the same horizon from the neutral/evidenced anchor with explicit events and residual disabled. The fallback must itself be projected or planned so every response step is feasible; never weaken `ControllerResponse` validation and never return a partially realized proposal.

- [ ] **Step 4: Verify GREEN and commit**

Run: `uv run pytest tests/models/test_package.py tests/motion/test_streaming.py tests/motion/test_controller_response.py -v && uv run ruff check src tests && uv run mypy src && git diff --check`

Commit: `fix: keep production motion feasible across replans`

### Task 2: Head gestures from arbitrary valid head poses

**Files:**
- Modify: `src/alice/motion/head_primitives.py`
- Modify: `src/alice/motion/streaming.py`
- Test: `tests/motion/test_head_primitives.py`
- Test: `tests/motion/test_streaming.py`

**Interfaces:**
- Consumes: `HeadGesture.initial_targets`, `HeadPrimitiveGenerator.render_window(...)`.
- Produces: absolute-phase gesture windows that preserve inactive axes at their original values and recover only the active semantic axis unless the gesture is `return_to_attention`.

- [ ] **Step 1: Add non-neutral-pose and cross-prefix regressions**

Create a nod beginning at `neck_rotation=0.01`, `head_tilt=0.0`, `face_pitch=0.02`. Assert inactive axes stay exactly at their initial values across the full gesture and two adjacent rendered windows join at the same absolute phase. Add an integration test in which a residual leaves an inactive head axis non-neutral before a gesture begins.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/motion/test_head_primitives.py tests/motion/test_streaming.py -v`

Expected: FAIL with `non-active head axes cannot change during gesture recovery`.

- [ ] **Step 3: Implement pose-relative recovery**

For ordinary nod/shake/tilt/look gestures, preserve each inactive semantic axis at its captured `initial_targets` value. Apply configured recovery only to the active axis. Keep `return_to_attention` as the explicit multi-axis recovery primitive. Validate controller derivatives on every axis that actually changes.

- [ ] **Step 4: Verify GREEN and commit**

Run: `uv run pytest tests/motion/test_head_primitives.py tests/models/test_head_scheduler.py tests/motion/test_streaming.py -v && uv run ruff check src tests && uv run mypy src && git diff --check`

Commit: `fix: resume head gestures from actual poses`

### Task 3: Exact controller-response provenance from training to package

**Files:**
- Modify: `src/alice/motion/controller_response.py`
- Modify: `src/alice/training/residual.py`
- Modify: `src/alice/models/package.py`
- Test: `tests/training/test_residual_training.py`
- Test: `tests/models/test_package.py`

**Interfaces:**
- Produces: `ControllerResponseConfig.response_sha256`, recorded by residual training metadata and required by package validation.

- [ ] **Step 1: Add exact-identity regressions**

Change only `neck_rotation.max_velocity_per_s` from `0.8` to `0.4` while retaining model/calibration/firmware identities. Assert training/package compatibility rejects the mismatch. Assert canonical serialization produces a stable response SHA and that actuator ordering or any fitted response parameter changes it.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/training/test_residual_training.py tests/models/test_package.py -v`

Expected: FAIL because firmware-settings SHA does not identify fitted response parameters.

- [ ] **Step 3: Bind exact response identity**

Hash the canonical full `ControllerResponseConfig` excluding no fields that affect runtime prediction. Store that SHA in the training record and package manifest/config identities. Reject mismatches before model construction. Keep `controller_settings_sha256` as the distinct firmware identity.

- [ ] **Step 4: Verify GREEN and commit**

Run: `uv run pytest tests/training/test_residual_training.py tests/models/test_package.py tests/motion/test_controller_response.py -v && uv run ruff check src tests && uv run mypy src && git diff --check`

Commit: `fix: bind residual training to exact response model`

### Task 4: Read-only connected-device and replay readiness gate

**Files:**
- Create: `src/alice/experiments/motion_readiness.py`
- Create: `src/alice/experiments/motion_readiness_cli.py`
- Modify: `pyproject.toml`
- Create: `hardware/bringup/streaming-affect-motion-v1.md`
- Test: `tests/experiments/test_motion_readiness.py`
- Test: `tests/packaging/test_installed_wheel.py`

**Interfaces:**
- Produces: `alice-motion-readiness --hardware-manifest ... --model-package ... --camera-device ... --json-output ...`.

- [ ] **Step 1: Add fail-closed, no-actuation readiness tests**

Test stable Pololu by-id identity resolution, stable V4L2 by-id camera identity, exact model/calibration/controller identities, and a 60-second deterministic mock-controller replay. Patch serial construction/write and camera frame opening to fail if invoked; the readiness command may inspect filesystem/sysfs/V4L2 metadata but must not open either device. Assert stale/missing/wrong devices and any replay exception produce a non-zero result and structured reason.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/experiments/test_motion_readiness.py tests/packaging/test_installed_wheel.py -v`

Expected: FAIL because the readiness entry point does not exist.

- [ ] **Step 3: Implement the explicit readiness command and concise procedure**

Load immutable config/package snapshots, resolve device symlinks and identities without opening them, run the production generator against the mock/controller-response model for 60 seconds over representative affect vectors and seeds, and emit a compact JSON report with hashes and pass/fail checks. Document the exact read-only command, expected evidence, and the later separate hardware command boundary. Do not add actuator execution to this CLI.

- [ ] **Step 4: Verify GREEN and commit**

Run: `uv run pytest tests/experiments/test_motion_readiness.py tests/packaging/test_installed_wheel.py -v && uv run ruff check src tests && uv run mypy src && git diff --check`

Commit: `feat: add streaming motion readiness gate`

### Final verification

- [ ] Run `uv run pytest -q`.
- [ ] Run `uv run ruff check src tests`.
- [ ] Run `uv run mypy src`.
- [ ] Run `git diff --check`.
- [ ] Run the explicit readiness CLI against the currently connected stable device paths with the servo master switch OFF; do not open serial/camera and do not issue actuator commands.
- [ ] Independently review the full repair range against the design spec and hardware-readiness objective.
