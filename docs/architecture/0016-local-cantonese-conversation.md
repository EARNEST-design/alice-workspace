# ADR 0016: Local female Cantonese speech

- Date: 2026-09-21
- Status: implemented for attended evaluation; operator accepted voice, pace and volume
- Authorization: operator requested local Cantonese replies and a female voice with a slower sample.

## Decision

With Cantonese configured, Qwen ASR admits English and Cantonese without the
English-only upload hint. Chinese metadata is an explicit compatibility alias
routed to Cantonese; the original tag remains visible. Correctly downmixed
synthetic probes returned Cantonese, so they do not establish a need for this
alias. Other languages, empty results and invalid streams never trigger replies.
Without Cantonese configuration, English-only admission is unchanged.

Remote Qwen is prompted for colloquial Hong Kong Cantonese in Traditional
Chinese, one sentence of at most 40 Chinese characters. This is a prompt limit;
enforced bounds remain 600 response characters, 200 clause characters and
15 seconds of output per clause. English keeps its short prompt and Azelma.
CJK sentence punctuation releases a clause without requiring whitespace.

Use `FunAudioLLM/CosyVoice-300M-SFT` revision
`fbb71de2afe387ed854eebd80b9f3d078c6b9869`, preset `粤语女`. Pin official source
`074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc` and Matcha-TTS submodule
`dd9105b34bf2be2230f4aa1e4769fb586a3c824e`. Run in isolated Python 3.12.14,
Torch/Torchaudio 2.8.0+cpu, six CPU threads, one interop thread, FP32 flow/vocoder
and dynamic INT8 Linear layers in the LLM. `requirements/cosyvoice-cpu.txt`
records the tested CPU subset; this differs from the upstream historical GPU
stack. The existing English environment stays unchanged.

Use OpenCC `t2s` to convert character forms before synthesis while preserving
Cantonese wording and the displayed Traditional Chinese reply. This improved
pronunciation in synthetic probes. Use accepted speed 0.85 and Python/NumPy/Torch
seed 7 per clause. Seeds do not guarantee identical output across runtimes.

Synthesize the complete clause, validate 22,050 Hz mono PCM and resample to
24 kHz. Normalize toward RMS 0.12, maximum gain 8 and peak 0.95, before the
unchanged 11572/32768 speaker multiplier. Near silence is not amplified. The
identity-selected ReSpeaker route and existing digital cap remain in force.

A persistent external process emits bounded JSON headers and float32 PCM.
The parent enforces header/chunk/sample/time limits and generation ownership.
Stop terminates the owned worker/player; stale cleanup cannot stop replacements.
Production text/audio stay in bounded memory; worker stderr is discarded and
no named production audio file is created. No model download occurs in a turn.
Both voices warm before live capture. ADR 0017 defines wake admission.

## Provenance and alternatives

The official model card declares Apache-2.0 and its shipped speaker table names
the female Cantonese preset. There is no operator voice cloning or training.
Underlying speaker consent and dataset lineage have not been independently
audited for public release. The initial Canto TTS Nano candidate was rejected
for quiet output and a male-sounding voice. A compact VITS alternative lacked
verified weight licensing and voice provenance. Pocket/Azelma stays for English.

- [Pinned model](https://huggingface.co/FunAudioLLM/CosyVoice-300M-SFT/tree/fbb71de2afe387ed854eebd80b9f3d078c6b9869)
- [Pinned source](https://github.com/QwenAudio/CosyVoice/tree/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc)

## Evidence and limits

See [the experiment](../experiments/2026-09-21-cantonese-conversation.md).
The accepted longer sample produced 6.15 seconds of audio, with 24.1 seconds
to first PCM including cold startup. Earlier warm short clauses at speed 1.0
took 2.8–5.1 seconds. These software timings are not acoustic latency or bounds
on every reply. This female CPU voice is slower than the rejected Nano model.
ASR round trips still contain pronunciation errors and are not human accuracy
scores. Sample acceptance does not qualify arbitrary text or live bilingual
conversation. Playback guard stays enabled; full duplex remains unqualified.

Final warm integration at speed 0.85 measured 8.864 seconds to first PCM for a
4.238-second generated reply; both-voice warmup was 16.297 seconds.
