# Cantonese conversation and addressed wake experiment, 2026-09-21

## Objective and authorization

The operator requested local Cantonese recognition, replies and speech, then
reported that the initial speech was quiet and male-sounding. They accepted a
replacement female sample at slower speed after requesting a longer sentence.
They also selected wake names Alice / 愛麗絲 / 爱丽丝 and a 30-second follow-up
window. All work remains in the existing streaming-affect-motion worktree.

## Configuration and provenance

Final decisions are ADRs 0016 and 0017; launch instructions are in
`docs/conversation-bench.md`. The model is official CosyVoice-300M-SFT revision
`fbb71de2afe387ed854eebd80b9f3d078c6b9869`, preset 粤语女, official source
`074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc`, Matcha-TTS submodule
`dd9105b34bf2be2230f4aa1e4769fb586a3c824e`. It declares Apache-2.0. Speaker
consent history and full training data lineage are not independently audited.
No operator cloning or training is performed.

The isolated Python 3.12.14 CPU environment has Torch/Torchaudio 2.8.0+cpu,
NumPy 1.26.4, six threads, one interop thread, dynamic INT8 Linear layers in the
LLM, FP32 flow/vocoder, OpenCC t2s character forms, speed 0.85 and seed 7.
The exact freeze is `requirements/cosyvoice-cpu.txt`; `uv pip check` passes for
109 packages. Main Pocket/Azelma remains on its existing environment.

The original Canto TTS Nano candidate and its historical probes are retained
privately as rejected experiments. A first round-trip probe accidentally
flattened stereo; its duration/pitch and language inference were invalid.
Corrected explicit downmix probes returned Cantonese. Chinese ASR metadata
remains an intentional compatibility alias, not a proven endpoint requirement.

## Measurements

| Probe | Observation |
| --- | --- |
| Three warm female clauses, speed 1.0, character conversion | 2.784, 5.088 and 3.480 s synthesis; 1.509, 2.740 and 1.950 s audio |
| Longer physical sample, speed 0.85 | 24.100 s to first PCM including cold startup; 147679 samples / 6.153 s at 24 kHz; native player drained |
| Operator listening | Accepted female voice, pace and volume of the longer sample |
| Both-voice warmup in final dry probe | 16.297 s |
| Synthetic final-ASR input → actual Qwen reply → actual female worker | 10.063 s total; reply complete 1.199 s; warm TTS first PCM 8.864 s; 101704 samples / 4.238 s output; dry_complete and refreshed wake window |
| Hosted Qwen ASR speech regressions | English and Cantonese passed; correct language tags |
| Later direct digital-silence probes | Server returned empty content with finish_reason=error, with and without English hint; client failed closed |

The accepted physical sample was “你好呀，我係愛麗絲。我而家可以用廣東話同你傾偈，你今日過得點呀？”.
Only synthetic audio/text was retained for these probes. The final dry probe
stubbed ASR with a synthetic final Cantonese transcript; it did not exercise the
microphone or hosted ASR. It did exercise actual Chinese wake matching, remote
Qwen, female worker, normalization, dry output and engagement refresh. Its first
run used an incorrect harness assertion for intermediate `dry` instead of
terminal `dry_complete`; the corrected probe passed.

Timings are software measurements, not acoustic latency or broad benchmarks.
ASR round trips contain pronunciation errors and are not human accuracy scores.
The female CPU voice is slower than the rejected Nano voice; dynamic INT8 and
six threads improve it, but low latency is not achieved for longer replies.
Initial direct MiniCPM follow-up probes returned WAIT for all three cases,
including clear English/Cantonese questions. Clarifying the trusted active
conversation context and adding bounded examples produced the expected result
in five final probes (three SPEAK, two WAIT; 252–690 ms). Four probes reuse
prompt examples: this is tuning/sanity evidence, not held-out accuracy. An
intermediate prompt incorrectly admitted an unfinished fragment; the final
negative example corrected that probe. Hard asleep/wake admission stays local.

## Verification and artifacts

The combined conversation and existing speech regression run passed 322 tests.
After the final prompt-only adjustment, the 46 model/runtime tests passed
again. Scoped Ruff, strict mypy, lock consistency and diff checks passed. Independent
review cleared Chinese prefix matching, decision-time expiry and external
worker spawn/cancellation ownership after deterministic regressions.

Ignored evidence root: `artifacts/conversation/2026-09-21-cantonese/`:

- `config.json`: complete final experiment configuration and consent scope.
- `metrics.json`: synthetic metrics and listening acceptance.
- `model-manifest.json`: SHA256/size of the eight pinned downloaded model assets.
- `manifest.json`: evidence and implementation checksums (written at completion).
- `female-long-playback.json`, `female-physical-output.json`: actual player evidence.
- `final-dry-smoke.json`: full synthetic event trace; no human transcript.
- `final-tests.log`, `final-review.md`: verification and independent review.
- `female-voice-research.md`: primary-source alternatives/provenance assessment.

Wake evidence is in `artifacts/conversation/2026-09-21-wake-gate/`, including
runtime-model-report, red/green logs, review and synthetic endpoint probes.
Model downloads and synthetic WAVs remain under private state/cache directories,
not the repository. Production microphone audio is never saved by this service;
in-memory dashboard/ROS transcripts are not copied into experiment artifacts.
No motor, firmware, commit, merge or push action was performed.

The reloaded production service passed a synthetic English WAV replay through
real VAD, hosted ASR, remote Qwen and dry Azelma output. It emitted dry_complete,
then stopped output/capture and cleared engagement during replay cleanup.
Final Stop left it idle; ROS reconnected. The browser showed both ASR languages
and both wake-enabled mode labels. See `reloaded-service-replay.json`.

## Conclusion

The operator accepted the female Cantonese voice, normalized volume and slower
pace. Bilingual routing and wake-based admission are implemented and exercised
with synthetic integration tests. Live speech recognition, arbitrary Cantonese
pronunciation, contextual follow-up accuracy and full-duplex echo handling still
need attended evaluation. The later upstream silence error remains visible and
fails closed; local VAD remains the first silence gate.
