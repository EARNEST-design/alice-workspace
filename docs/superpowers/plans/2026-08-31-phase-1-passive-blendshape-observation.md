# Phase 1 Passive Blendshape Observation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible, hardware-free camera pipeline that measures named MediaPipe blendshapes on Alice's stationary face and reports observation stability.

**Architecture:** A camera adapter supplies frames to a versioned perception adapter, which emits immutable named observations to a JSONL run store. Offline analysis reads only the run artifacts and produces stability metrics and an experiment conclusion; neither perception nor analysis imports actuator code.

**Tech Stack:** Python 3.12, uv, OpenCV, MediaPipe Tasks, NumPy, Pydantic 2, pytest, Ruff, mypy

**Spec:** `docs/architecture/0002-emotion-to-expression-learning-pipeline.md`

## Global Constraints

- Alice remains stationary throughout Phase 1; no actuator device is opened or commanded.
- Store derived blendshape observations by default; raw image retention defaults to disabled.
- Every observation preserves category names, timestamps, confidence, camera identity, detector/model identity, and validity state.
- Every experiment has a config, manifest, metrics, artifact checksums, and short conclusion.
- Tests use synthetic frames or recorded consent-safe fixtures and never discover live hardware.
- Dependency versions are locked with `uv.lock`; generated datasets and videos are not committed.

---

### Task 1: Python project and observation contracts

**Files:**
- Create: `pyproject.toml`
- Create: `src/alice/__init__.py`
- Create: `src/alice/contracts/__init__.py`
- Create: `src/alice/contracts/blendshapes.py`
- Create: `tests/contracts/test_blendshapes.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `BlendshapeObservation`, `BlendshapeScore`, `ObservationValidity`, and `validate_category_schema(observation, expected_names)`.

- [ ] **Step 1: Write failing contract tests**

```python
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from alice.contracts.blendshapes import (
    BlendshapeObservation,
    BlendshapeScore,
    ObservationValidity,
    validate_category_schema,
)


def valid_observation() -> BlendshapeObservation:
    return BlendshapeObservation(
        schema_version="blendshape-observation/v1",
        captured_at=datetime(2026, 8, 31, tzinfo=UTC),
        monotonic_ns=10,
        camera_id="alice-face-webcam",
        run_id="passive-001",
        detector="mediapipe-face-landmarker",
        detector_model_sha256="a" * 64,
        image_width=640,
        image_height=480,
        face_confidence=0.9,
        validity=ObservationValidity.VALID,
        invalid_reason=None,
        scores=(BlendshapeScore(name="eyeBlinkLeft", score=0.1),),
    )


def test_observation_rejects_duplicate_category_names() -> None:
    source = valid_observation().model_dump()
    source["scores"] = [
        {"name": "eyeBlinkLeft", "score": 0.1},
        {"name": "eyeBlinkLeft", "score": 0.2},
    ]
    with pytest.raises(ValidationError, match="unique"):
        BlendshapeObservation.model_validate(source)


def test_invalid_observation_requires_reason() -> None:
    source = valid_observation().model_dump()
    source.update(validity="no_face", face_confidence=None, invalid_reason=None, scores=[])
    with pytest.raises(ValidationError, match="invalid_reason"):
        BlendshapeObservation.model_validate(source)


def test_category_schema_is_order_independent_but_name_exact() -> None:
    observation = valid_observation()
    validate_category_schema(observation, ("eyeBlinkLeft",))
    with pytest.raises(ValueError, match="missing=.*jawOpen"):
        validate_category_schema(observation, ("eyeBlinkLeft", "jawOpen"))
```

- [ ] **Step 2: Run the tests and verify the missing-package failure**

Run: `uv run pytest tests/contracts/test_blendshapes.py -v`

Expected: FAIL because `alice.contracts.blendshapes` does not exist.

- [ ] **Step 3: Add project tooling and implement the contracts**

Configure `pyproject.toml` with Python `>=3.12,<3.14`, a `src` package layout, runtime dependencies `numpy`, `opencv-python-headless`, `mediapipe`, and `pydantic>=2,<3`, plus test dependencies `pytest`, `ruff`, and `mypy`. Implement frozen Pydantic models, score bounds `[0, 1]`, SHA-256 format validation, unique names, and the validity/reason invariant. Add `/artifacts/`, `/data/`, `.venv/`, and MediaPipe model binaries to `.gitignore`.

- [ ] **Step 4: Lock dependencies and run quality checks**

Run: `uv lock && uv run pytest tests/contracts/test_blendshapes.py -v && uv run ruff check src tests && uv run mypy src`

Expected: all commands exit 0.

- [ ] **Step 5: Commit the contract foundation**

```bash
git add .gitignore pyproject.toml uv.lock src/alice tests/contracts
git commit -m "feat: add versioned blendshape observation contract"
```

---

### Task 2: Camera and MediaPipe adapters

**Files:**
- Create: `src/alice/perception/__init__.py`
- Create: `src/alice/perception/camera.py`
- Create: `src/alice/perception/mediapipe_adapter.py`
- Create: `src/alice/perception/cli.py`
- Create: `tools/fetch_mediapipe_model.py`
- Create: `config/models/mediapipe-face-landmarker-v1.yaml`
- Create: `tests/perception/test_camera.py`
- Create: `tests/perception/test_mediapipe_adapter.py`
- Create: `tests/perception/test_cli.py`
- Create: `tests/tools/test_fetch_mediapipe_model.py`
- Create: `tests/fixtures/perception/face_result.json`

**Interfaces:**
- Consumes: `BlendshapeObservation` and `BlendshapeScore` from Task 1.
- Produces: `CapturedFrame`, `FrameSource.read() -> CapturedFrame`, `MediaPipeBlendshapeAdapter.observe(frame, run_id) -> BlendshapeObservation`, and the read-only `alice-camera list` command.

- [ ] **Step 1: Write failing adapter tests**

```python
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from alice.perception.camera import CapturedFrame
from alice.perception.mediapipe_adapter import MediaPipeBlendshapeAdapter


class FakeDetector:
    def detect_scores(self, _rgb: np.ndarray) -> tuple[float, list[tuple[str, float]]]:
        return 0.88, [("jawOpen", 0.2), ("eyeBlinkLeft", 0.1)]


def test_adapter_preserves_names_and_sorts_canonical_schema(tmp_path: Path) -> None:
    model = tmp_path / "face.task"
    model.write_bytes(b"fixture-model")
    adapter = MediaPipeBlendshapeAdapter(
        camera_id="alice-face-webcam", model_path=model, detector=FakeDetector()
    )
    frame = CapturedFrame(
        captured_at=datetime(2026, 8, 31, tzinfo=UTC),
        monotonic_ns=42,
        bgr=np.zeros((2, 3, 3), dtype=np.uint8),
    )
    observation = adapter.observe(frame, run_id="passive-001")
    assert [item.name for item in observation.scores] == ["eyeBlinkLeft", "jawOpen"]
    assert observation.image_width == 3
    assert observation.image_height == 2
    assert observation.detector_model_sha256 != ""
```

Also test camera open/read failures with an injected fake `cv2.VideoCapture` factory and verify no numeric device is opened during test collection. Test that `alice-camera list` reports injected camera capabilities without importing actuator modules. Test that the model-fetch tool rejects a payload whose SHA-256 differs from the value in its manifest and atomically installs a matching payload.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/perception -v`

Expected: FAIL because the perception adapters do not exist.

- [ ] **Step 3: Implement injected, side-effect-free adapters**

Implement `OpenCVCamera` so construction stores configuration but `open()` performs device access explicitly. Require an explicit device path or index in configuration. Implement the MediaPipe wrapper behind an injected detector protocol so tests do not load a model. Convert BGR to RGB, hash the model file, canonicalize scores by name, and emit `no_face` observations rather than stale prior values. Add the `alice-camera` entry point to `pyproject.toml`; its `list` subcommand may enumerate video devices but must never import or inspect serial hardware.

- [ ] **Step 4: Pin and fetch the detector artifact reproducibly**

Record the official MediaPipe model URL, published model/version identity, verified SHA-256, retrieval date, and permitted-use reference in `config/models/mediapipe-face-landmarker-v1.yaml`. The fetch tool accepts only that manifest, downloads to a temporary file, validates SHA-256 before rename, and prints the installed artifact path and hash. Tests use an injected byte stream and never access the network.

- [ ] **Step 5: Verify adapter behavior**

Run: `uv run pytest tests/perception tests/tools/test_fetch_mediapipe_model.py -v && uv run ruff check src tests tools && uv run mypy src`

Expected: all commands exit 0.

- [ ] **Step 6: Commit the adapters**

```bash
git add pyproject.toml src/alice/perception tools/fetch_mediapipe_model.py config/models/mediapipe-face-landmarker-v1.yaml tests/perception tests/tools tests/fixtures/perception
git commit -m "feat: add explicit camera and blendshape adapters"
```

---

### Task 3: Reproducible passive capture runs

**Files:**
- Create: `src/alice/experiments/__init__.py`
- Create: `src/alice/experiments/manifest.py`
- Create: `src/alice/experiments/passive_capture.py`
- Create: `config/experiments/passive-alice-face.example.yaml`
- Create: `tests/experiments/test_passive_capture.py`
- Create: `docs/experiments/README.md`

**Interfaces:**
- Consumes: `FrameSource` and `MediaPipeBlendshapeAdapter` from Task 2.
- Produces: `PassiveCaptureConfig`, `ArtifactManifest`, and `run_passive_capture(config, frame_source, observer, output_dir) -> ArtifactManifest`.

- [ ] **Step 1: Write failing run-store tests**

```python
import json
from pathlib import Path

from alice.experiments.passive_capture import PassiveCaptureConfig, run_passive_capture


def test_capture_writes_manifest_observations_and_checksums(
    tmp_path: Path, fake_frame_source, fake_observer
) -> None:
    config = PassiveCaptureConfig(
        run_id="passive-001",
        sample_count=3,
        sample_interval_ms=0,
        retain_frames=False,
    )
    manifest = run_passive_capture(config, fake_frame_source, fake_observer, tmp_path)
    lines = (tmp_path / "observations.jsonl").read_text().splitlines()
    assert len(lines) == 3
    assert not list(tmp_path.glob("*.png"))
    assert manifest.artifacts["observations.jsonl"].sha256
    assert json.loads((tmp_path / "manifest.json").read_text())["run_id"] == "passive-001"
```

Add tests that reject `retain_frames=True` unless `retention_approval` is a non-empty identifier, and that an interrupted run writes `status="aborted"` without inventing a conclusion.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/experiments/test_passive_capture.py -v`

Expected: FAIL because the experiment modules do not exist.

- [ ] **Step 3: Implement atomic JSONL and manifest output**

Write observations to a temporary file in the run directory, flush and `fsync`, then rename on normal completion. Record command configuration, Git revision, dependency-lock checksum, platform information, timestamps, artifact SHA-256 values, and final status. Do not store environment variables or serial-device contents in the manifest.

- [ ] **Step 4: Add the example passive configuration and operating instructions**

The example configuration names the camera but does not assume its numeric index, uses 640×480 at 10 samples/second for 120 seconds, disables frame retention, and labels camera placement and lighting as required operator-entered run metadata. Document a read-only camera-discovery command separately from capture.

- [ ] **Step 5: Verify capture behavior**

Run: `uv run pytest tests/experiments/test_passive_capture.py -v && uv run ruff check src tests && uv run mypy src`

Expected: all commands exit 0.

- [ ] **Step 6: Commit passive capture**

```bash
git add src/alice/experiments config/experiments tests/experiments docs/experiments
git commit -m "feat: record reproducible passive blendshape runs"
```

---

### Task 4: Stability analysis and Phase 1 acceptance report

**Files:**
- Create: `src/alice/analysis/__init__.py`
- Create: `src/alice/analysis/blendshape_stability.py`
- Create: `tests/analysis/test_blendshape_stability.py`
- Create: `docs/experiments/templates/passive-blendshape-conclusion.md`
- Modify: `src/alice/experiments/passive_capture.py`

**Interfaces:**
- Consumes: Phase 1 `observations.jsonl` and `manifest.json`.
- Produces: `StabilityMetrics`, `analyze_stability(run_dir) -> StabilityMetrics`, and `phase_1_acceptance(metrics, thresholds) -> AcceptanceResult`.

- [ ] **Step 1: Write failing statistical tests with deterministic fixtures**

```python
from alice.analysis.blendshape_stability import analyze_observations


def test_stability_reports_detection_and_per_category_variance(valid_observations) -> None:
    metrics = analyze_observations(valid_observations)
    assert metrics.total_frames == 4
    assert metrics.valid_frames == 3
    assert metrics.detection_rate == 0.75
    assert metrics.categories["jawOpen"].mean == 0.2
    assert metrics.categories["jawOpen"].standard_deviation == 0.0
```

Add exact tests for percentile range, warm-up-versus-steady drift, missing categories, and an acceptance result that lists every failed threshold rather than returning one Boolean.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest tests/analysis/test_blendshape_stability.py -v`

Expected: FAIL because stability analysis does not exist.

- [ ] **Step 3: Implement deterministic metrics**

Use NumPy with explicit population standard deviation (`ddof=0`). Report detection rate, count, mean, standard deviation, median, 5th/95th percentiles, warm-up drift, and lag-1 autocorrelation for each named category. Treat missing categories as schema errors, not zeros.

- [ ] **Step 4: Add a conclusion template and CLI output**

The conclusion records camera placement, lighting, model hash, thresholds, results, anomalies, privacy/retention state, and a categorical `pass`, `fail`, or `inconclusive`. Threshold values live in the experiment config and are justified from pilot data; the code ships no fabricated universal threshold.

- [ ] **Step 5: Run the complete Phase 1 verification suite**

Run: `uv run pytest -v && uv run ruff check src tests && uv run mypy src && git diff --check`

Expected: all commands exit 0 and no test opens a camera or serial device.

- [ ] **Step 6: Commit stability analysis**

```bash
git add src/alice/analysis src/alice/experiments tests/analysis docs/experiments
git commit -m "feat: analyze passive blendshape stability"
```

---

### Task 5: Conduct the passive Alice-face pilot

**Files:**
- Create: `config/experiments/passive-alice-face-pilot.yaml`
- Create: `docs/experiments/passive-alice-face-pilot.md`
- External artifact: `artifacts/passive-alice-face-pilot/`

**Interfaces:**
- Consumes: Phase 1 capture and analysis commands.
- Produces: reviewed camera placement, run manifest, stability metrics, and Phase 1 conclusion.

- [ ] **Step 1: Discover camera identity without opening actuators**

Run an implementation-provided `alice-camera list` command and record device path, USB identity when available, supported modes, and the chosen stable identifier in the pilot config. Verify separately that no `/dev/ttyACM*` path is opened by the process.

- [ ] **Step 2: Review privacy and placement metadata**

Set `retain_frames: false`, point the webcam at Alice's full face, record distance, angle, lighting, resolution, focus/exposure mode, and confirm no participant is in frame.

- [ ] **Step 3: Run three stationary captures**

Capture one warm-up run and two fresh-process repeat runs. Do not touch Alice or change lighting between the repeat runs. Store outputs under ignored `artifacts/passive-alice-face-pilot/`.

- [ ] **Step 4: Generate and review the conclusion**

Run stability analysis across each run and between repeat runs. Record which blendshapes provide signal, which are noise-dominated, detection failures, camera changes needed, and whether Phase 1 passes, fails, or is inconclusive.

- [ ] **Step 5: Commit only provenance-safe configuration and conclusion**

```bash
git add config/experiments/passive-alice-face-pilot.yaml docs/experiments/passive-alice-face-pilot.md
git commit -m "exp: conclude passive Alice face observation pilot"
```

Do not commit raw frames, video, generated JSONL data, or machine-specific device nodes.
