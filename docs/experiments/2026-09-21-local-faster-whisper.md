# Local faster-whisper alongside MiniCPM

Date: 2026-09-21. English first, as selected by the operator.
Status: local inference benchmark completed; persistent live service, dashboard
and ROS telemetry are proposed, awaiting design approval. No new microphone
capture or playback occurred in this benchmark. Existing operator-consented
recordings stayed local and private. No robot motion occurred.

## Result and selected starting configuration

Use faster-whisper 1.2.1, CTranslate2 4.8.2, `base.en`, CPU INT8, four threads,
one worker, English, beam size 1, temperature 0 and no previous-text conditioning.
The model revision is `3d3d5dee26484f91867d81cb899cfcf72b96be6c` from
`Systran/faster-whisper-base.en`; the model card specifies MIT.

An isolated Python 3.13 environment was installed under the private hardware
state directory. The reviewed repository environment and lockfile were unchanged.
The recognizer was exercised while `minicpm5-2b` stayed resident locally; six
trials also issued an overlapping MiniCPM request. Models/services are distinct:
MiniCPM receives text, while faster-whisper performs speech recognition.

| Condition | ASR median, seven-second synthetic clip | Concurrent MiniCPM median |
| --- | ---: | ---: |
| Two threads, ASR only | 711.90 ms | — |
| Two threads, concurrent request | 727.53 ms | 210.19 ms |
| Four threads, ASR only | 446.10 ms | — |
| Four threads, concurrent request | 489.42 ms | 224.85 ms |

Each condition has only three repetitions, using the same short synthetic
introduction. This measures feasibility and local contention, not a production
latency percentile or held-out recognition accuracy. All twelve warm trials
matched the known synthetic text. Both prompted operator phrases were recognized
from the earlier recording; four-thread cropped decodes took 333–334 ms.
The benchmark process reached approximately 391 MiB peak RSS. This excludes
MiniCPM and TTS memory. Model construction took about 203 ms in the four-thread
condition, with a first decode of 458 ms; this was a warm filesystem/cache test.

## Silence and VAD findings

Without VAD, five seconds of digital silence produced a spurious word. Never
use nonempty Whisper text alone as evidence of speech. The entire ungated
20-second microphone clip also produced more text than the two short crops;
there is no independently annotated full-capture reference.

The ONNX VAD bundled with faster-whisper rejected digital silence, but detected
only one of the two phrases at the selected thresholds. It is not interchangeable
with the previously inspected Silero 6.2.2 artifact merely because both are named
Silero VAD.

Loading the official `silero_vad.onnx` from the pinned silero-vad 6.2.2 wheel
directly through ONNX Runtime recovered both phrases and rejected digital
silence. Its SHA256 is
`1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
This hash must be checked against the saved artifact metadata before reuse.
One ONNX inference thread took a median 0.090 ms and p95 0.106 ms per 512-sample
frame on the operator replay. Threshold 0.5, release 0.35, 200 ms pre-roll and
300 ms silence ended both segments after 320 ms measured from the first
below-release frame start. Timestamp conventions add frame duration when
comparing arrival/processing time; this is not acoustic endpoint latency.

The official ONNX replay fed those two segments to faster-whisper, correctly
recognizing the prompted phrases in approximately 373–395 ms each. No ASR was
called for digital silence. Keep explicit VAD admission and qualify room noise,
speaker echo, double-talk and varied speech separately.

## Remote Qwen completion

Download job `job_bd27f04e26` completed at 08:16:47 UTC with 16,081,498,220 bytes.
`qwen3.8-27b-mlx`, MLX 4-bit, was loaded through the Mac's API with context 8192;
loading took 20.646 seconds. No UI operation or existing GPT-OSS unload was needed.

The raw Hugging Face model entry does not expose a reasoning setting: native
`reasoning: off` returned HTTP 400. OpenAI-compatible chat accepted the probe
with `reasoning_effort: none` and `chat_template_kwargs.enable_thinking: false`,
but emitted reasoning anyway. A 128-token probe yielded visible answer text
after 2813 ms; an 80-token native probe exhausted its budget on reasoning and
whitespace. Do not count whitespace as a first useful token or speak reasoning.

A text-only `/v1/completions` request manually rendered the model's own template
with `enable_thinking=false`, including its empty thinking prefix. This produced
a visible answer with no thinking block. First visible non-whitespace text took
696 ms initially and 461/461 ms in two warm repeats; warm total response time
was 762 ms. These are same-prompt synthetic smoke measurements, not a claim
about varied conversation. This workaround is per-request and leaves global
model configuration unchanged. A permanent adapter needs template pinning,
context rendering and streamed-content filtering tests before reuse.

## Evidence and continuation

Aggregate config, dependency/model hashes, individual trials, endpoint requests,
responses, metrics, manifest and conclusion are in
`artifacts/conversation/2026-09-21-faster-whisper/` (ignored local artifacts).
Raw audio and operator transcript evidence are outside the repository under
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/`.

Private inference environment: `faster-whisper-env/bin/python` under that root.
The disposable benchmark is `faster-whisper-benchmark.py`; it is diagnostic
code, not a continuously running service. The `silero-vad` wheel was installed
without dependencies for its model assets; its Torch-dependent top-level Python
API was not imported. ONNX Runtime inference was tested directly.

The [proposed bench design](../superpowers/specs/2026-09-21-live-conversation-bench-design.md)
defines the next implementation and its verification. Preserve the existing
speech/3D worktree and the accepted USB 2.0.7 hardware baseline. No new commits,
merges or pushes were made for these probes.

Sources: [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[Silero VAD](https://github.com/snakers4/silero-vad),
[LM Studio native chat](https://lmstudio.ai/docs/developer/rest/chat), and
[Qwen model template](https://huggingface.co/lmstudio-community/Qwen3.8-27B-MLX-4bit/blob/main/chat_template.jinja).
