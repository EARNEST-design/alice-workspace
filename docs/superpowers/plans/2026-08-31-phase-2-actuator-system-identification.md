# Phase 2 Actuator System Identification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and execute a mock-first, explicitly armed experiment that measures repeatable actuator-to-blendshape effects without allowing perception or experiment code to bypass safety supervision.

**Architecture:** A stateful experiment runner submits named pose requests to a safety supervisor, which validates them against a checksummed hardware manifest before forwarding them to an injected actuator adapter. The runner synchronizes actuator status with Phase 1 observations, stores every transition, and analyzes variance, hysteresis, coupling, latency, and local Jacobians.

**Tech Stack:** Python 3.12, NumPy, Pydantic 2, PyYAML, pyserial behind an injected transport, pytest

**Spec:** `docs/architecture/0002-emotion-to-expression-learning-pipeline.md`

## Global Constraints

- Mock and replay adapters are the defaults; importing or constructing an adapter never opens a serial device.
- Hardware completion is a three-stage prepare/execute/finalize lifecycle; execute returns an unpublished `pending_power_removal` capability and only a fresh bound power-OFF confirmation publishes `COMPLETED`.
- The hardware root constructs the exact C525/MediaPipe observer internally; low-level Python APIs are not a malicious in-process security boundary.
- No hardware task runs without a separately reviewed bring-up procedure and contemporaneous user approval.
- Commands use semantic actuator names and carry schema, calibration, timestamp, expiry, and run identity.
- Dimension mismatch, non-finite values, stale commands, unknown identities, and out-of-limit targets fail closed.
- Ordinary tests and CI cannot enable hardware.
- Every actuator experiment starts and ends at the reviewed safe pose when communication remains available.

---

### Task 1: Semantic hardware manifest and pose contracts

**Files:**
- Create: `src/alice/contracts/actuation.py`
- Create: `src/alice/hardware/__init__.py`
- Create: `src/alice/hardware/manifest.py`
- Create: `hardware/alice-face-v1.yaml`
- Create: `tests/contracts/test_actuation.py`
- Create: `tests/hardware/test_manifest.py`

**Interfaces:**
- Produces: `ActuatorTarget`, `PoseRequest`, `ActuatorStatus`, `HardwareManifest`, and `load_manifest(path) -> HardwareManifest`.

- [ ] **Step 1: Write failing schema and manifest tests**

Test that channel identities are unique, channel 7 is absent, controller serial is `00037376`, normalized zero maps to each named Home value, channel 10 carries an inspection-required flag, and channel 2 records firmware maximum `8832` separately from the conservative software maximum `8000`. Test rejection of unknown actuator names, non-finite targets, duplicate names, expired requests, and calibration-hash mismatch.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/contracts/test_actuation.py tests/hardware/test_manifest.py -v`

Expected: FAIL because the actuation contracts and manifest loader do not exist.

- [ ] **Step 3: Implement contracts and transcribe reviewed hardware facts**

Use the semantic functions and values in `hardware/motor-map.md` and `hardware/reference-motor-calibration.md`. Preserve evidence references in YAML. Do not infer missing electrical, current, emergency-stop, or linkage facts; encode them as unmet preflight requirements rather than values.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/contracts/test_actuation.py tests/hardware/test_manifest.py -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/contracts/actuation.py src/alice/hardware hardware/alice-face-v1.yaml tests
git commit -m "feat: add semantic actuator manifest and contracts"
```

---

### Task 2: Safety supervisor state machine

**Files:**
- Create: `src/alice/safety/__init__.py`
- Create: `src/alice/safety/supervisor.py`
- Create: `tests/safety/test_supervisor.py`

**Interfaces:**
- Consumes: `PoseRequest`, `ActuatorStatus`, and `HardwareManifest`.
- Produces: `RunState`, `SafetyFault`, `SafetySupervisor.preflight()`, `.arm(approval)`, `.authorize(request)`, `.watchdog(now_ns)`, and `.acknowledge_fault()`.

- [ ] **Step 1: Write a failing transition-table test suite**

Cover `DISARMED -> PREFLIGHT -> ARMED -> RUNNING`, and every transition to `ABORTING` or `FAULTED`. Assert that missing approval, failed linkage inspection, competing-process preflight failure, controller error, stale command, excessive step, excessive rate, wrong calibration identity, and watchdog expiry prevent forwarding. Assert that only explicit acknowledgement after restored preconditions leaves `FAULTED`.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/safety/test_supervisor.py -v`

Expected: FAIL because the supervisor does not exist.

- [ ] **Step 3: Implement the minimal deterministic state machine**

Use a monotonic clock injected into the supervisor. Return structured authorization or fault results; do not log-and-continue. Keep the supervisor independent of ROS, MediaPipe, OpenCV, and serial libraries.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/safety -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/safety tests/safety
git commit -m "feat: enforce actuator safety state machine"
```

---

### Task 3: Mock and disconnected Maestro adapters

**Files:**
- Create: `src/alice/hardware/adapter.py`
- Create: `src/alice/hardware/mock_adapter.py`
- Create: `src/alice/hardware/maestro_protocol.py`
- Create: `src/alice/hardware/maestro_adapter.py`
- Create: `tests/hardware/test_mock_adapter.py`
- Create: `tests/hardware/test_maestro_protocol.py`
- Create: `tests/hardware/test_maestro_adapter.py`

**Interfaces:**
- Produces: `ActuatorAdapter.apply(request) -> ActuatorStatus`, `MockActuatorAdapter`, `encode_set_target(channel, target_qus) -> bytes`, and `MaestroAdapter.open(explicit_enable_token)`.

- [ ] **Step 1: Write failing protocol and no-open tests**

Assert `encode_set_target(6, 5059) == bytes([0x84, 6, 67, 39])`. Use an injected fake serial transport to test complete writes, short reads, timeouts, error-register handling, and deterministic close. Monkeypatch the real serial constructor to raise if module import, adapter construction, or mock tests attempt to open a device.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/hardware/test_mock_adapter.py tests/hardware/test_maestro_protocol.py tests/hardware/test_maestro_adapter.py -v`

Expected: FAIL because adapters do not exist.

- [ ] **Step 3: Implement adapters without automatic fallback**

Require an explicit stable device path, controller serial match, and enable token for `open()`. Never substitute the mock adapter after a hardware error. Batch only contiguous channels with tested protocol encoding; otherwise use individual Set Target messages.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/hardware -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/hardware tests/hardware
git commit -m "feat: add mock-first Maestro actuator adapters"
```

---

### Task 4: System-identification runner and artifact store

**Files:**
- Create: `src/alice/experiments/system_identification.py`
- Create: `config/experiments/actuator-identification-mock.yaml`
- Create: `tests/experiments/test_system_identification.py`

**Interfaces:**
- Consumes: Phase 1 observer and `SafetySupervisor`; the public mock root
  constructs the exact `MockActuatorAdapter` internally.
- Produces: `IdentificationConfig`, `IdentificationStep`, and
  `run_mock_identification(config, observer, supervisor, output_dir, clock,
  sleeper) -> ArtifactManifest`.
- Keeps the deterministic execution core private so a later trusted
  composition root may reuse it without exposing arbitrary adapter injection.

This supersedes the original generic `run_identification(..., adapter, ...)`
signature. Review demonstrated that a caller-supplied adapter could delegate to
hardware while presenting mock provenance, so no public composition root may
accept an arbitrary adapter object or adapter factory.

- [ ] **Step 1: Write failing end-to-end mock tests**

Given one mock actuator and offsets `0.1` and `-0.1`, assert the exact sequence `home, +0.1, home, -0.1, home`; repeated observations occur only after actuator and visual settling; each Home is independently verified; commands and observations share step IDs; and camera loss, controller fault, timeout, or excessive visual variance aborts before the next movement.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/experiments/test_system_identification.py -v`

Expected: FAIL because the runner does not exist.

- [ ] **Step 3: Implement the runner as a deterministic step machine**

Inject clock, sleeper, observer, and supervisor; construct the exact mock
adapter inside the public mock root. Store commands, observed positions,
blendshape observations, settling samples, transitions, and faults in
append-only JSONL artifacts with checksums. The mock configuration exercises
all mapped channels but cannot select the Maestro adapter.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/experiments/test_system_identification.py -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/experiments/system_identification.py config/experiments tests/experiments
git commit -m "feat: add guarded actuator identification runner"
```

---

### Task 5: Identification statistics and Jacobian uncertainty

**Files:**
- Create: `src/alice/analysis/system_identification.py`
- Create: `tests/analysis/test_system_identification.py`
- Create: `docs/experiments/templates/actuator-identification-conclusion.md`

**Interfaces:**
- Produces: `IdentificationMetrics`, `estimate_local_jacobian(samples)`, and `compare_repeat_run(reference, repeat) -> RepeatabilityResult`.

- [ ] **Step 1: Write failing deterministic analysis tests**

Use a synthetic two-actuator, three-blendshape fixture with known slopes. Assert recovery of central-difference slopes, per-position variance, bootstrap confidence intervals with seed `20260831`, hysteresis, cross-effects, settling time, and a rank/condition-number warning for an unobservable dimension.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/analysis/test_system_identification.py -v`

Expected: FAIL because analysis does not exist.

- [ ] **Step 3: Implement analysis and machine-readable metrics**

Use session-grouped samples, preserve actuator and blendshape names, emit NumPy arrays only internally, and serialize named matrices with explicit row/column labels. Report uncertainty rather than replacing missing effects with zero.

Use the versioned population variance definition (`ddof=0`) consistently.
Serialize named monotonicity, one-sided slope asymmetry, and outer/inner slope
saturation formulas. A single-magnitude protocol must report saturation as
typed missing/inconclusive.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/analysis/test_system_identification.py -v && uv run ruff check src tests && uv run mypy src`

```bash
git add src/alice/analysis/system_identification.py tests/analysis docs/experiments/templates
git commit -m "feat: analyze actuator blendshape effects"
```

---

### Task 6: Write and review the hardware experiment procedure

**Files:**
- Create: `hardware/bringup/phase-2-actuator-identification.md`
- Create: `config/experiments/actuator-identification-hardware.yaml`
- Modify: `hardware/README.md`

**Interfaces:**
- Consumes: the tested Phase 2 implementation and verified Phase 1 conclusion.
- Produces: a run-specific preflight checklist, conservative offsets, abort
  criteria, recovery procedure, and a separate capability-gated hardware
  composition/entrypoint that constructs the exact Maestro adapter from the
  reviewed configuration. It accepts no arbitrary adapter object or factory,
  though it may reuse Task 4's private deterministic core.

The hardware composition must own an independent watchdog and permit-revocation
path; process-local clock failure cannot be allowed to preserve motion authority.

- [ ] **Step 1: Draft the procedure from verified manifests**

Include controller serial, stable device path, motor/calibration hash, speed and acceleration limits, initial offset, Home tolerance, settling limits, watchdog duration, camera acceptance criteria, E-stop/power-removal procedure, channel 10 linkage inspection, physical-clearance check, and competing-process check.

- [ ] **Step 2: Prove CI cannot select the hardware configuration**

Add a test that the hardware config requires a run-specific approval identifier absent from repository defaults and that the ordinary CLI refuses hardware mode without both an enable flag and interactive confirmation.

- [ ] **Step 3: Run the complete hardware-free suite**

Run: `uv run pytest -v && uv run ruff check src tests && uv run mypy src && git diff --check`

Expected: all commands exit 0 with the Maestro disconnected or inaccessible.

- [ ] **Step 4: Commit the proposed procedure**

```bash
git add hardware/bringup/phase-2-actuator-identification.md hardware/README.md config/experiments/actuator-identification-hardware.yaml tests
git commit -m "docs: propose guarded actuator identification run"
```

- [ ] **Step 5: Stop for explicit procedure and hardware-run approval**

Do not execute the hardware configuration in the same approval turn used to merge its implementation. Present the exact procedure, configuration checksum, test results, detected controller identity, and unresolved preflight items to the user.

---

### Task 7: Execute and conclude the approved actuator experiment

**Files:**
- Create: `docs/experiments/actuator-identification-first-run.md`
- External artifact: `artifacts/actuator-identification-first-run/`

**Interfaces:**
- Produces: approved run manifest, measured effects, repeat-run comparison, faults/anomalies, and Phase 2 conclusion.

- [ ] **Step 1: Reconfirm approval and all preflight evidence immediately before execution**

Abort without movement if any required item is missing, changed, or fails. Record the aborted result rather than weakening the gate.

- [ ] **Step 2: Run the smallest approved single-actuator sequence**

Observe Home, positive offset, Home, negative offset, and Home. Review controller errors, camera validity, mechanical behavior, current/noise observations available to the operator, and returned Home state before authorizing the next actuator.

- [ ] **Step 3: Run approved remaining actuators and a fresh-session repeat**

Advance only within the reviewed configuration. Any larger offset or coupled-actuator sequence requires a new reviewed configuration and approval.

- [ ] **Step 4: Analyze and write the conclusion**

Record repeatable effects, noise-dominated dimensions, hysteresis, coupling, unsafe or unreliable channels, Jacobian uncertainty, and a `pass`, `fail`, or `inconclusive` Phase 2 result.

- [ ] **Step 5: Commit only provenance-safe conclusions and configuration**

```bash
git add config/experiments/actuator-identification-hardware.yaml docs/experiments/actuator-identification-first-run.md
git commit -m "exp: conclude actuator blendshape identification"
```

Do not commit raw video, participant data, machine device nodes, or generated bulk artifacts.
