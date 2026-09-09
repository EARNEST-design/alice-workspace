# Local Speech Synchronization Implementation Plan

**Goal:** Local Pocket TTS audio and sample-aligned mouth/affect composition.
**Architecture:** Bounded utterance preparation, explicit PCM clock, independent
speech/affect contracts and deterministic semantic composition.
**Spec:** `docs/architecture/0007-local-speech-and-affect-synchronization.md`.
**Tech stack:** Python 3.13, NumPy, Pydantic 2, optional Pocket TTS and sounddevice.

## Constraints

Preserve existing uncommitted motion work. No hardware actuation. No inferred
affect-to-motor mappings. No text sent to remote inference. Tests precede behavior.
Artifact manifests record seed, config, package/model identity and checksums.

## Execution

- [x] Contract and timeline: add `contracts/speech.py`, `speech/timeline.py` and
  synthetic PCM tests. Reject empty/oversized/nonfinite plans, unordered cues and
  geometry mismatch. Resolve two unequal-length segments and pauses in samples;
  verify silence, smoothing, terminal release and exact affect interpolation.
- [x] Composition: add `speech/composer.py`; test cumulative sparse expression
  updates, preserved non-jaw channels and speech ownership during silence. Emit
  the existing `AffectIntent` from resolved cue values.
- [x] Synthesis: add `speech/synthesis.py`; lazy optional CPU model, isolated RNG,
  explicit January model/preset voice, cached model state, bounded PCM and offline
  mode. Verify adapter arguments with a narrow external-model double.
- [x] Playback and artifacts: add `speech/playback.py`, `speech/cli.py` and preview
  resource. Verify DAC timing, callback failures, cancellation and clean end using
  a fake device; CLI export tests inspect real WAV/JSON/checksums and escaped text.
- [x] Integration: optional dependency lock, example LLM plan/schema, container
  entry point, runbook and hardware timing unknowns. Run real local synthesis,
  offline repeat, all tests, Ruff, mypy, wheel build and independent code review.

Run focused checks with `uv run --extra speech pytest tests/speech -q`, then
`uv run --extra speech pytest -q`, `uv run ruff check .`,
`uv run mypy src/alice`, and `git diff --check`. Keep changes in the existing
`feature/streaming-affect-motion` worktree for user review.
