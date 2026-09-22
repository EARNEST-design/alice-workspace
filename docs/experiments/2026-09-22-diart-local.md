# Local Diart trial, 2026-09-22

Diart runs locally within the measured compute budget. Speaker categories are
promising, but initial/new-speaker coverage remains insufficient for dependable
live attribution. The trial is installed and reproducible; Alice's live decision
pipeline was not changed.

## Measured outcome

The fixed model combination is Diart incremental clustering, pyannote
segmentation-3.0 ONNX and WeSpeaker ResNet34 ONNX. Five-second causal rolling
windows advance every 500 ms. Torch and each ONNX session use one computation
thread. All tests use CPU; no remote diarization or inference downloads occur.

| Setting | Development gates | Untouched confirmation gates |
|---|---:|---:|
| 0.5 s buffering, upstream thresholds | 4/7 | 0/4 original held out |
| 1.0 s buffering, same thresholds | 6/7 | 1/2 fresh held out |

The short ambiguous case is unscored and produced no speaker label. Gates were
fixed beforehand: correct count, 70% coverage of each turn's central span,
80% dominant-category purity, and correct same/different-speaker relations.
Source placements are exact but include internal TTS pauses; coverage, DER and
overlap are construction proxies, not gold human-speech accuracy measures.

Counts and raw dominant-category relations were correct throughout the tested
clear synthetic turns. At 0.5 s, new voices often received labels only after much
of the turn passed; recurring turns generally reached about 98% coverage.
Increasing buffering to 1.0 s improved that without changing clustering thresholds.
One fresh A→B→A conversation passed; the fresh B→A→B conversation failed because
its first B turn reached only 61.25% coverage. Later B returned to the same category
with 98.42% coverage. All six fresh turns had 100% dominant-category purity among
labelled frames. The legacy short single-voice control still failed development.
These are small preset-voice tests, not real-person or Cantonese qualification.

![Source placements and emitted speaker categories](../../artifacts/conversation/2026-09-22-diart/speaker-timeline.png)

Speech fixtures typically required 90–165 ms compute per 500 ms update. Across
corrected initial development/held-out trials, maximum compute was 210 ms,
per-file p95 at most 200 ms, process high-water RSS about 622 MiB and no update
took 500 ms. Fresh 1.0 s confirmation took 165–170 ms p95 with about 616 MiB peak
RSS. Buffering delay is additional to compute; these numbers are not end-to-end
speech response latency. Initial pipeline construction took about 3.25 seconds.
Models ran alongside the existing installed bench services, not during a sustained
worst-case simultaneous TTS workload. RSS is lifetime process high-water, not
incremental per-model memory.

A separate 20-second attended ReSpeaker microphone probe used 40 half-second
frames, a five-second memory ring and 1.0 s buffering. Median/p95/max compute was
37.0/38.6/39.5 ms; capture completion drift across the run was 23.8 ms. Input RMS
reached 0.00286, no speaker labels were produced, and no error occurred. The owned
capture process stopped. This verifies quiet-input scheduling only; it does not
establish live two-person attribution, acoustic response or worst-case backlog.
No audio, embeddings, per-person tracks or transcripts were saved from that run.

## Does speaker context fix the decision model?

Eight authored context pairs were queried with the existing conservative prompt;
no production prompt or endpoint configuration was changed. These use manually
specified ideal speaker categories, not measured Diart output. The richer arm
adds both overheard text and labels; it cannot isolate the benefit of labels.

Qwen matched 7/8 full-context expectations and still falsely replied to one
person-to-person answer. MiniCPM matched 5/8 and returned SPEAK for every case,
including both peer-conversation negatives and the no-wake control. In one Qwen
case, adding context changed an ambiguous peer-directed question from SPEAK to
WAIT. Without context two cases were deliberately underidentified; do not score
those as known correct/incorrect or claim an overall accuracy gain. Reply content
was not generated or evaluated. The deterministic live wake gate separately blocks
the no-wake case before a model call.

This supports further testing of explicit heard-turn context, but neither ideal
labels nor more text alone fixes admission. Unknown/stale attribution and direct
addressing still need explicit handling before live integration.

## Provenance and implementation

- Diart source: `392d53a1b0cd67701ecc20b683bb10614df2f7fc`, MIT.
- WeSpeaker: `hbredin/wespeaker-voxceleb-resnet34-LM`, revision
  `0ae88dcaf48cacdf741275d6d1a8101f45eee220`, Apache-2.0; ONNX SHA256
  `7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068`.
- Existing segmentation export SHA256
  `220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079`.
- Isolated Python 3.12.14, Torch/TorchAudio 2.8 CPU, pyannote.audio 3.3.2,
  ONNX Runtime 1.23.2, NumPy 1.26.4. Matplotlib 3.8.4 avoids the removed
  `get_cmap` dependency that broke initial import. Main dependencies are unchanged.
- CPU requirements: `requirements/diart-cpu-py312.txt`. Model/license hashes:
  `artifacts/conversation/2026-09-22-diart/model-manifest.json`.
- Original 12 fixtures total 158.13 s; fresh two total 24.78 s. Pocket TTS
  Azelma/Alba presets, distinct English phrases and file-specific recorded seeds;
  source/model/voice hashes in manifests. Inference seed 7. Original development
  and held-out source phrases are disjoint; fresh confirmation uses six new phrases.
- Every WAV lives outside the repository under
  `/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/diart/fixtures/`.

Independent review caught Diart's repeated startup prefix at timestamp zero.
The trial now clips output to each finalized publication interval; a regression
reproduced the failure before the fix. Superseded outputs are preserved and were
not used for final metrics. Review verified exact powerset conversion against
pyannote on 879 random frames, model/batch contracts, thread settings and temporal
bounds. Small frame-resolution gaps remain in the measured output, not hidden.

WeSpeaker converts Diart weights to binary masks and excludes overlapping frames
when constructing embeddings. Results qualify this particular combination only.
Upstream clustering can assign a short unmatched fragment to an existing category;
there is no calibrated speaker confidence. Keep such attribution unknown in any
future consumer rather than interpreting the cluster number as proof.

## Reproduce

The trial never opens the microphone or speaker. Explicit synthetic WAV paths
and the two pinned local models are required. Run in the preserved worktree:

```bash
/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/diart/env/bin/python \
  scripts/diart_trial.py \
  --segmentation /home/alice/.local/state/alice-hardware/respeaker-usb-20260921/overlap/sherpa-onnx-pyannote-segmentation-3-0/model.onnx \
  --embedding /home/alice/.local/state/alice-hardware/respeaker-usb-20260921/diart/models/wespeaker/speaker-embedding.onnx \
  --latency 1.0 --output-dir /tmp/alice-diart-replay \
  --synthetic-wavs /home/alice/.local/state/alice-hardware/respeaker-usb-20260921/diart/fixtures/13-extra-heldout-a-b-a.wav
```

The ignored artifact directory contains configs, raw synthetic outputs, scoring
scripts/reports, frozen manifests, oracle-context requests/results, red/green
regression logs and redacted live timing. Negative results are retained. No
hardware/firmware/motion changes, live decision-policy changes, commit or push.

Final verification: **407 conversation/speech tests passed in 34.26 s**; all five
new adapter regressions passed, Ruff/format/diff checks passed. Independent
review has no unresolved important findings after the publication-window fix.
