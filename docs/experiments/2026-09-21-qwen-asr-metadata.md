# Qwen ASR metadata handling during background sound

The operator reported `invalid Qwen ASR metadata prefix` during silence or
background sound. The current server loaded the previous no-speech fix, but
the parser still required an exact English or None prefix. The operator's
failing audio/response had not been retained. A 30-second listen-only diagnostic
window triggered no new ASR request, so it did not reproduce that particular
room event.

An actual endpoint probe with reversed, previously generated Azelma speech
returned `language Arabic<asr_text>` plus 40 characters despite `language=en`.
The old parser rejects that valid metadata with the reported error. This
reproduces the client/server incompatibility without uploading retained human
audio. Ordinary synthetic noise returned None; English input returned English.
The exact language tag on the earlier operator event remains unknown.

[Qwen's reference parser](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/utils.py)
describes language metadata, no-speech output, and whitespace-separated headers.
The updated client recognizes its canonical language names, waits for a complete
bounded header across SSE chunks, and separates wire validity from the selected
English policy. Non-English text is never emitted as a partial. After successful
stop/DONE, an empty result carries the detected language and skip reason. The
runtime shows `ASR ignored` and never calls either text model or the speaker for
that result. English transcription and the existing None handling are preserved.

The metadata limit is 128 characters, excluding a pending fragment of the text
marker; text and SSE/deadline bounds remain in force for ignored streams too.
Malformed/unknown headers, nonempty no-speech output, and truncated/failed
streams still fail. No plain-text fallback was added to this metadata-emitting
endpoint. No raw microphone data or operator transcript was saved.

Actual post-fix endpoint checks (one trial each, not latency percentiles):

| Synthetic input | Result | Total request time |
| --- | --- | ---: |
| Reversed Azelma speech | One empty ignored Arabic final; zero partials | 131.91 ms |
| Original English Azelma speech | Valid English final, 76 characters | 88.19 ms |
| Five seconds of digital silence | One empty None final | 28.45 ms |

Verification: **102 conversation tests pass**, including regressions for
fragmentation, whitespace/case, supported language tags, suppression of partial
text, finality, UI explanation, and the metadata boundary. Ruff lint/format and
strict mypy on all ten modules pass. Independent scoped review has no unresolved
findings. The service was reloaded with this fix and left idle for the operator.

Config remains seed 7, temperature 0, Qwen ASR 1.7B on the configured vLLM 0.14.0
endpoint, and the existing local Silero admission settings. This fixes metadata
handling; it does not establish that all background sound is rejected by VAD
or that a decoder can never invent English text. No changes to the advisory
MiniCPM policy, echo guard, playback or motor interfaces are included.

Evidence: ignored `artifacts/conversation/2026-09-21-qwen-asr-metadata/` contains
synthetic probe metrics, failing and passing tests, review, source/artifact
manifest and the short conclusion. Original integration evidence is preserved.
