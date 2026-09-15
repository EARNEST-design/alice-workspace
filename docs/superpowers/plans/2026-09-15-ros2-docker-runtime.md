# ROS 2 Docker Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the existing Alice speech/affect/face pipeline as eight separate ROS 2 nodes in an offline Docker Compose project.

**Architecture:** Keep the existing tested Python library, add typed ROS interfaces and thin process adapters, and preserve DAC-clock provenance through expression and composition to the guarded serial owner. Start with deterministic simulation; use separate explicit speaker/hardware overlays. Treat the pending physical face trial as a separate acceptance issue.

**Tech Stack:** ROS 2 Lyrical, Ubuntu 26.04, Python 3.14/rclpy, ament/colcon, Docker Compose, existing Pocket TTS/MediaPipe/NumPy/Alice library.

**Spec:** `docs/superpowers/specs/2026-09-15-ros2-docker-runtime-design.md` (user approved 2026-09-15).

## Global Constraints

- Preserve worktree `feature/streaming-affect-motion`, existing CLI behavior and all artifacts; no merge, push, or device access during implementation.
- Base image: `ros:lyrical-ros-base-resolute@sha256:dbb2a254523ee3c40ec9fc07956bc1042253c7beb3b6dad4a4585d85c99e9716`.
- System Python 3.14 for ROS; qualify dependencies before widening `>=3.12,<3.14`.
- Separate nodes/services: session, tts, audio, expression, motion, maestro, perception, recorder. Runtime `internal: true` network, Fast DDS UDP, no host IPC/privileged mode/Docker socket.
- One active run, immutable generation/epoch identity, original monotonic timestamps, 250 ms source/progress/gap limits, no command replay or fault-to-success transition.
- Two-second PCM ring, 200 ms startup prebuffer, 20 ms transport chunks, bounded producer queues and cumulative sample credits including in-flight data.
- Retain 100 ms mouth lookahead, channels 6/3/4/5/9/11 only, jaw 0/0 then successful Home restoration to 0/11, 25 s hardware run/10 s audio/2 s maximum sad hold/6 s pose deadline.
- Tests precede new behavior. Record actual Docker builds, ROS graph tests, fault results and image identities; do not equate PWM with physical movement.
- No subagents from implementers/reviewers. The coordinator owns reviews and integrations.

### Task 1: Qualify Python 3.14 and create the container base

**Files:** `pyproject.toml`, `uv.lock`, `.dockerignore`, `infra/ros2/Dockerfile`, `infra/ros2/entrypoint.sh`, `infra/ros2/requirements*.txt`, `tests/ros2/test_packaging.py`, `docs/experiments/2026-09-15-ros2-python-qualification.md`.

**Consumes:** Pinned base image and current locked package/model versions; existing `src/alice`, tests and configs.
**Produces:** `alice-ros2:base` plus Docker build targets `core`, `speech`, `perception`, `test`; portable `/opt/alice/config`, `/opt/alice/hardware`, `/opt/alice/src`, system-compatible virtualenv `/opt/alice/venv`; no devices at build/run. Later tasks add colcon packages. Keep shared lightweight dependencies importable without Torch/MediaPipe.

- [ ] Write packaging/import tests before changing metadata. Probe Python 3.14 in a disposable container copy, not the checked-in Python constraint. Record exact import/inference failures.
- [ ] Retain direct dependency versions; resolve a Python 3.14 lock and run the existing suite in the pinned Lyrical container. Verify pinned CPU Torch, MediaPipe model opening and real offline Azelma synthesis against mounted caches; synthetic substitute is not inference qualification.
- [ ] On success widen support to `<3.15`, move camera dependencies into a documented `perception` extra, ensure `dev` includes what the full test suite needs, and preserve a full-install path via extras. Avoid importing optional ML from the servo path.
- [ ] Add explicit build-context exclusions and pinned install inputs. Image entrypoint sources `/opt/ros/lyrical/setup.bash` and, when present, `/opt/alice/ros/install/setup.bash`, then `exec`s argv. Use no host ROS install.
- [ ] Test lightweight and full image imports, record output under `artifacts/ros2/2026-09-15/`, update qualification report, inspect diff and commit.

Packaging test example (adapt actual interpreter/container wrapper, keep assertions):

```python
def test_core_imports_without_ml():
    import subprocess, sys
    result = subprocess.run([sys.executable, '-c',
        'from alice.speech.face_runtime import FaceRuntime; '
        'import sys; assert "torch" not in sys.modules; '
        'assert "mediapipe" not in sys.modules'], check=False)
    assert result.returncode == 0
```

### Task 2: Define ROS wire contracts and reusable run/transport guards

**Files:** `ros2_ws/src/alice_interfaces/{CMakeLists.txt,package.xml,msg/*,srv/*,action/*}`, `ros2_ws/src/alice_nodes/{setup.py,setup.cfg,package.xml,resource/alice_nodes,alice_nodes/__init__.py,alice_nodes/contracts.py,alice_nodes/transport.py}`, `tests/ros2/test_transport.py`, `tests/ros2/test_conversions.py`.

**Consumes:** Existing `SpeechClause`, `SpeechFrame`, `TargetUpdate`, `BlendshapeObservation` validation; base image from Task 1.
**Produces:** Generated `alice_interfaces` typed messages/services/actions; pure Python `alice_nodes.transport` guards, conversions in `alice_nodes.contracts`. Exact node-to-node API is committed by this task and used by Task 3.

- [ ] Write failing tests for immutable run identity, expired/future timestamps, monotonic sequences, fault latching, cumulative credits (duplicate credit cannot enlarge window), malformed/NaN PCM and clause ordering. Example behavior:

```python
def test_terminal_fault_cannot_be_completed():
    guard = RunGuard(run_id='r', epoch='e', generation_id='g')
    guard.fail('lost playback')
    with pytest.raises(ValueError):
        guard.complete()
```

- [ ] Define `RunIdentity` (run_id, epoch, generation_id), `StreamHeader` (identity, sequence, source_monotonic_ns), `SpeechClause`, `PcmChunk`, `PcmCredit`, `SpeechState`, `ExpressionFrame`, `FaceTarget`, `RunHealth`, `PlaybackStatus`, `ServoReceipt`, `FaceObservation` and bounded auxiliary data. Carry full clause metadata in the first PCM packet so cross-topic ordering cannot race admission. Subsequent packets identify the clause and global sample offset.
- [ ] Define `BeginRun` carrying identity, selected profile, seed, hardware flag, sad hold and clock-domain identity; `EndRun` with explicit terminal outcome; `RunSpeech` action for mounted named fixture or external committed-clause source. Include accepted/error results and action progress. Use idempotent terminal handling.
- [ ] Implement converters through existing Pydantic contracts and the pure transport guards. Reject unknown schema, calibration, nonfinite values, wrong dimensions, duplicate/out-of-order streams and oversize messages before state mutation. Stale epochs are ignored, not admitted as new runs.
- [ ] Build `alice_interfaces`/`alice_nodes` with colcon and run round-trip tests using real generated classes in Docker. Add typed validation tests for all consumed control messages, inspect diff and commit.

### Task 3: Implement all runtime nodes against the typed contracts

**Files:** `ros2_ws/src/alice_nodes/alice_nodes/{base.py,session.py,tts.py,audio.py,expression.py,motion.py,maestro.py,perception.py,recorder.py,cli.py}`, `tests/ros2/test_nodes.py`, `tests/ros2/test_lifecycle.py`, targeted extraction in `src/alice/experiments/face_speech_cli.py` and `src/alice/speech/` only when necessary to avoid a second hardware implementation.

**Consumes:** Exact Task 2 wire definitions and guard/conversion APIs; the existing algorithms listed in the design.
**Produces:** Eight runnable entrypoints (`session`, `tts`, `audio`, `expression`, `motion`, `maestro`, `perception`, `recorder`) and a `run` CLI client. Every process starts idle without motion, supports bounded cleanup, and writes bounded local evidence.

- [ ] Write tests for prepare/start order, one active run, schema/config identity agreement, node restart rejection, sibling fault propagation, no stale source timestamp refresh, and successful PCM drain required before final pose/Home. Tests must fail with absent node implementation.
- [ ] Implement shared node plumbing: parameters for installed config/hardware/output roots, immutable incarnation, explicit startup clock-domain verification, bounded queues, 50 ms run health, local progress watchdogs, latched failure and idempotent EndRun. Use a dedicated worker for inference/serial/log flushes and keep DDS or file I/O out of PortAudio callback.
- [ ] Implement session action coordination. Prepare mandatory participants, warm TTS before Maestro activation, establish peers/leases, stream validated clauses from a mounted fixture or bounded external source, cancel on error, and wait for recorder finalization before claiming complete evidence. A new run requires explicit action admission; container restart never resumes motion.
- [ ] Implement TTS (deterministic synthetic mode default, real warm `PocketTtsWorker` offline mode), credit-limited 20 ms PCM, and audio with the existing timeline/ring/playback. A PCM packet's first-clause metadata is validated before playback; underflow cancels immediately locally. Emit only actual DAC-aligned `SpeechState` and distinguish generated/submitted/played counts.
- [ ] Implement expression from the original speech frame, motion from that same embedded frame, and Maestro from validated proposals. Preserve original source time, reject ages over 250 ms at every hop, and retain serial-owner/watchdog and local cancellation. Completion must match successful current-run playback; no Home or restoration on fault. Device ownership stays exclusively in Maestro.
- [ ] Implement replay/C525 perception using current detector contracts and explicit no-face validity. Recorder collects derived ROS events plus existing local audio/servo logs, manifests/config/model/code/image hashes and terminal outcomes; raw retention opt-in. Never add closed-loop servo correction.
- [ ] Run unit/generated-interface tests and a local eight-process ROS launch smoke run with synthetic audio/replay/mock servos; assert all node names, bounded sample flow, completion and receipts. Inspect diff and commit.

### Task 4: Package, qualify and document the complete Compose deployment

**Files:** `infra/ros2/{Dockerfile,compose.yaml,compose.audio.yaml,compose.hardware.yaml,.env.example,fastdds.xml}`, `ros2_ws/src/alice_bringup/`, `tests/ros2/{test_compose.py,integration_runner.py}`, `docs/ros2.md`, `docs/architecture/0012-ros2-docker-runtime.md`, `docs/experiments/2026-09-15-ros2-runtime.md`, `SESSION_CHECKPOINT.md`.

**Consumes:** Runnable Task 3 nodes and Task 1 images.
**Produces:** Tested one-command Docker Compose default simulation, explicit speaker/hardware overlays, build/run/fault evidence, reproducible runbook and checkpoint.

- [ ] Write integration checks for all eight separate containers on the internal bridge; no default devices/privileges/host IPC/network. Build the project and test DDS UDP discovery on the actual bridge rather than only same-container launch.
- [ ] Add non-root/read-only/capability-dropped services, correctly scoped volumes, stable serial/C525 mappings and selected audio route, numeric device groups, offline cache and local artifacts. Add explicit installed-resource roots, launch files, health diagnostics, configuration snapshots and config/model digests. No broad device mount or automatic hardware arming.
- [ ] Exercise synthetic success and cancellation, stale/gapped/duplicate transport, PCM backpressure, callback underflow, controller fault, delayed expression, node kill/restart and recorder failure. Check no late commands/recovery after fault; measure source-to-admission age and stop latency. Confirm each queued resource and run duration is bounded.
- [ ] Run real offline Azelma through the Compose graph with simulated DAC and compare sample accounting/invariants to the baseline. Inspect the existing speaker route read-only and test the explicit audio overlay with simulated servos if available. Do not run servos while their prior physical-motion issue is unresolved.
- [ ] Run full Python regressions, ROS contract/launch/integration checks, Ruff/mypy where applicable and `git diff --check`. Document exact commands/results and limits. Leave a hardware command ready for the next attended test, without asserting physical acceptance.
- [ ] Request whole-migration review, fix material findings and verify fixes. Update plan/checkpoint/ADR and artifact manifest. Commit, preserve the branch and artifacts, and report build/test evidence plus outstanding physical acceptance.

## Execution record

No tasks complete yet. Baseline commit for migration code: `3f4093b`.
