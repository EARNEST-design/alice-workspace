# Incremental speech and emotion integration

Execution resumed 2026-09-10 in the preserved `feature/streaming-affect-motion`
worktree at clean `d9d9270`. Existing local speech/camera artifacts are preserved.

## Rebaseline and expression source

- Baseline: `uv run pytest -q` → 799 passed, two existing fork warnings (33.26 s).
- `uv run ruff check src tests` → passed; `uv run mypy src/alice` → 73 files passed.
- Workspace search, including ignored artifacts, found no `.safetensors`, `.pt`,
  `.pth`, or retained training JSON package. No newer source commits exist.
- `config/affect/intent-filter-v1.yaml` has no demonstrated support coordinates;
  `procedural-motion-v1.yaml` has no affect mappings. The retained replay uses
  explicitly zero residual weights. Package digest, training provenance and
  evaluation status: unavailable because no fitted package was found.
- Selected integration demonstration source: **authored-expression/v1** using
  reviewed smile/frown/neutral anchors and procedural blinks. No support points
  will be invented. Learned mode remains the existing honest neutral fallback.
- Installed Pocket TTS 3.1.0 `generate_audio_stream(model_state, text_to_generate,
  ..., copy_state=True)` yields tensors incrementally. Its implementation copies
  voice state and explicitly documents that a model is not thread-safe.

## Execution checklist

- [x] Rebaseline and select an explicit expression source.
- [ ] Immutable incremental clause contract and lazy fixture.
- [ ] Isolated warm TTS worker, bounded messages and cancellation.
- [ ] Bounded playback and streaming envelope on the DAC clock.
- [ ] Persistent expression bridge and retained composed replay.
- [ ] Selected face executor and disconnected qualification.
- [ ] Operator-requested physical combined-motion trial.

Accepted hardware comparison remains Azelma, 100 ms mouth lead, calibrated
channel 6 range, and jaw-only runtime 0/0. Physical combined-motion acceptance
is separate from software integration evidence.
