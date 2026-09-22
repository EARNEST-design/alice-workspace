# Local Diart trial implementation plan

> Execute the bounded trial in the preserved worktree. Use the perception-ML
> and MLOps evaluation roles; independent fixture preparation runs in parallel.

**Goal:** Test local, persistent speaker tracking for Alice without moving audio
processing elsewhere or replacing the accepted audio pipeline prematurely.

**Architecture:** Isolated Python 3.12 / CPU Torch 2.8 / pinned Diart source.
Reuse the existing licensed segmentation-3.0 ONNX export and use the supported,
public Apache-2.0 WeSpeaker ONNX embedding. Feed causal rolling windows into
Diart's actual incremental clustering; speaker labels are session-local estimates.

**Spec:** User approved the preceding proposal for local diarization alongside
Qwen ASR, speaker-labelled recent context, and evaluation before activation.
This trial first tests compute and label quality. No speaker identity enrollment.

## Constraints and acceptance

- Keep models, generated WAVs and isolated dependencies outside the repository.
- Keep existing service, ASR endpoint, voices, wake policy and hardware unchanged
  during qualification. No microphone/audio output required for synthetic tests.
- No human PCM, embeddings or transcripts stored in artifacts. Synthetic fixtures
  have preset voice/model provenance, hashes and seed 7.
- CPU-only, one Torch/ONNX thread initially; 0.5 s streaming steps, 5 s model
  window, 0.5 s initial aggregation delay. Record compute separately from delay.
- Use untouched Diart defaults for initial thresholds. Any calibration must use
  development fixtures only; preserve initial failures and frozen held-out inputs.
- Measure median/p95/max processing per step, process RSS, real-time factor,
  missed/false speech and speaker-confusion/fragmentation on A→B→A controls.
- Passing compute alone does not qualify speaker correctness. Unknown short or
  overlapping speech must not be forced into a confident identity.

## Tasks

- [x] Inspect upstream Diart interfaces and local resource availability.
- [x] Create isolated environment and download pinned public embedding/license.
- [x] Test adapter powerset mapping, causal window bounds and annotation clipping
  before keeping benchmark implementation. Use literal seven-class expectations
  and samples with distinct per-step values to expose accidental future access.
- [x] Implement `scripts/diart_trial.py`: explicit local model paths, hash checks,
  bounded WAV inputs, actual Diart streaming windows, JSON metrics and RTTM.
- [x] Prepare independent fixed development and held-out synthetic fixtures.
- [x] Run development trial, record failures, and if necessary one documented
  calibration before held-out evaluation. Do not tune to held-out output.
- [x] Compare speaker-labelled and unlabelled decision context on fixed cases
  if the audio trial can supply trustworthy labels.
- [x] Record model/source/env provenance, config, metrics, artifact manifest,
  conclusion and both checkpoints. Decide from evidence whether to attach live
  context or leave a runnable trial pending additional qualification.

## Verification

Run trial adapter tests in the isolated environment; run fixed WAVs through the
real ONNX models and Diart. Explicitly verify silence and speaker return identity.
Static-check the new script. If the live runtime is changed, add regression tests
for lifecycle reset, bounded context, ambiguous assignments and stale results,
then run the conversation/speech suite and an actual dry pipeline replay.

Setup evidence: pinned ONNX accepts dynamic samples; a 5-second input returned
293×7 frames. Use Diart's upstream 5-second default instead of the initially
considered 10-second window. Strict WAV validation rejected the older stereo
smoke fixture; independent trial fixtures are explicitly mono. No runtime
relaxation was made. Isolated matplotlib pinned to 3.8.4 for pyannote.core 5.0.0
compatibility; existing application dependencies were not changed.

Completion: local Diart tested with default0.5s and one1.0s delay alternative.
Default corrected development4/7 and originalheldout0/4; alternativedevelopment6/7
and independently frozenfreshconfirmation1/2. No classifier threshold tuning.
Initial originalheldout was run concurrently before development scoring; therefore
it was not reused to qualify the alternative. Synthetic oracle context was tested
separately from measuredDiart labels. Localmicrophone20sprobe keptup quietly.
No liveintegration: measuredinitial-turncoveragefails qualification. Final407tests
pass; completeexperimentreport/ADR/checkpoints record the reproducible result.
