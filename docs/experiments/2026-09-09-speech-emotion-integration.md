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
- [x] Immutable incremental clause contract and lazy fixture.
- [x] Isolated warm TTS worker, bounded messages and cancellation.
- [x] Bounded playback and streaming envelope on the DAC clock.
- [x] Persistent expression bridge and retained composed replay.
- [ ] Selected face executor and disconnected qualification.
- [ ] Operator-requested physical combined-motion trial.

Accepted hardware comparison remains Azelma, 100 ms mouth lead, calibrated
channel 6 range, and jaw-only runtime 0/0. Physical combined-motion acceptance
is separate from software integration evidence.

## Qualified software milestone

Implementation revision: `83f6b1d4194e93fc43fe46019ae45d9af547aecc`.
Tasks 1–5 reach the plan's permitted software checkpoint. Task 6, including its
trusted selected-face executor and physical trial, is not implemented or claimed.

- Full suite: **845 passed**, two existing fork warnings, 33.76 seconds.
- Ruff: passed. Mypy: passed, 80 source files. `git diff --check`: passed.
- Independent review reproduced four issues, now fixed: transient-event drift,
  delayed PCM invalidation on cancellation, fault-release artifact failure, and
  native decoder stalls under bounded backpressure. Review found no remaining
  critical issue in Tasks 1–5 after the fixes.
- Synchronization-event tests show first playback while both first-clause TTS
  and a fixture LLM source remain unfinished. This qualifies the AsyncIterable
  boundary; no live LLM provider SDK was added or benchmarked.

Real Pocket TTS/Azelma with actual PortAudio output and simulated servo proposals:

| Measurement | Qualified speaker run |
| --- | ---: |
| Generated/played samples at 24 kHz | 185,760 / 185,760 |
| Audio including closure tail | 7.74 s |
| First generated PCM from cold worker | 2.760 s |
| First DAC onset, mapped to host monotonic time | 3.120 s |
| First clause final chunk | 3.361 s |
| Output underflows | 0 |
| Maximum PCM ring occupancy | 48,000 samples / 2 s |
| Mouth lead | 100 ms |

Audio starts before first-clause TTS finishes. DAC timestamps are PortAudio's
clock observations; acoustic arrival and mechanics were not measured. The
accepted physical jaw configuration files are unchanged from `d9d9270`.

The separate same-seed cold/warm worker experiment emitted 29 chunks and 55,680
samples per request, with bit-identical float32 PCM. First chunk was 2.925 s cold
and 0.0885 s warm; final chunk was 3.505 s cold and 0.670 s warm. These are local
observations with concurrent test activity, not a latency guarantee.

The corrected composed replay shows corner transitions from +0.224 to -0.242,
blink excursion -0.426 and gaze excursion -0.143. All three head channels stay
zero. Before repair, transient accumulation drove eyelids to -1; that exploratory
run is retained but superseded by `azelma-speakers-qualified`.

Raw, held proposal traces are deliberately **not hardware commands**. The jaw's
largest sampled step is 0.942 and sampled rate is 45.12/s, above the accepted
command caps (0.4 and 10/s). Task 6 must use the jaw guard and separately derived
limits for each other selected channel; interpolation/holding at 50 Hz does not
establish those command bounds. Full per-channel derivatives are retained.

## Artifacts and reproduction

Evidence root: `artifacts/speech/integration-2026-09-10/`. The root manifest covers
test/red logs, review, cold/warm evidence, and all run files with SHA-256 hashes.
The qualified run retains source text, sync/jaw configs, complete code hashes,
chunk timing and PCM hashes, generated WAV, composed targets, expression state,
per-channel derivatives, metrics, its own manifest, and a self-contained replay.

```bash
HF_HUB_OFFLINE=1 uv run --extra speech alice-speak \
  --clauses config/speech/stream-demo-v1.jsonl \
  --expression-mode authored --play --output artifacts/speech/NEW-RUN
```

Omit `--play` for a simulated speaker clock. Without `--expression-mode authored`,
incremental playback uses learned neutral fallback. Both modes have no servo
authority. The existing `--plan` offline replay and jaw-only trial are retained.

Next: Task 6's candidate allowlist is mouth 6, corners 9/11, forehead 5, eyelids
3/4. Eyes 8/10 and head/neck 0/1/2 remain outside that initial physical scope.
Document and qualify their individual caps, one serial owner, latest-target
coalescing, receipts and fault/Home behavior before an operator-requested run.
