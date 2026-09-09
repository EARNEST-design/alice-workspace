# ADR 0007: Local speech and affect synchronization

- Status: implemented baseline; physical synchronization requires measurement
- Date: 2026-09-09
- Extends: ADR 0003 and the coordinated-motion direction in ADR 0005

## Decision

Run Kyutai Pocket TTS locally on CPU, behind an optional speech extra. Pin the
package and select `english_2026-01` explicitly to match the requested January
model. Use a supplied preset voice; custom voice cloning is outside this change.
Model and voice loading are explicit, lazy operations. Cache them for subsequent
utterances. First setup may download weights; subsequent offline runs must fail
clearly if any required artifact is missing. No text goes to an inference service.

The operator selected **Azelma** on 2026-09-09 after hearing local Fantine,
Éponine and Azelma auditions. Use Azelma when a plan omits a voice and in the
checked-in demos; preserve explicit voice choices in existing plans. It is the
upstream VCTK p303 preset, not a custom clone or a claim about speaker age.

An upstream LLM can emit a versioned `SpeechPlan`: source identity, seed, preset
voice, and ordered text segments. Each segment contains continuous valence,
arousal, dominance and intensity cues at normalized progress positions. Resolve
progress against the actual synthesized segment duration. These are approximate
segment-relative cues, not word alignments. Short clause segments give the LLM
useful emphasis boundaries without predicting TTS speed. Explicit pauses become
silence in PCM. The LLM does not generate motor targets or audio timestamps.

The implemented baseline pre-synthesizes a bounded utterance before playback.
ADR 0009 records the operator-requested replacement with incremental LLM and
PCM streaming; that next path is not implemented yet. This costs initial latency
but supplies deterministic sample offsets, prevents generation stalls during
playback, and makes cancellation and replay straightforward. Incremental TTS and
forced phoneme alignment are future optimizations of these same boundaries.

The audio sample position is the common clock for affect and mouth movement.
Derive a bounded mouth-aperture envelope from short PCM RMS windows with a noise
gate and attack/release smoothing. Silence closes the mouth. Speech owns the jaw
during playback; existing expression proposals retain eyes, brows, mouth corners,
and head movement. Expression jaw influence is attenuated and gated by speech
activity, so an open-mouth expression cannot hold the jaw open through silence.
At the end, release speech ownership back to the expression proposal.

Expose affect cues as existing `AffectIntent` messages for the motion pipeline.
The checked-in affect support set is empty: the default preview must identify
neutral expression fallback rather than invent trained emotion behavior. Accept
an optional relative-time expression horizon from the existing generator and
compose sparse updates cumulatively. Composition produces semantic targets only;
it neither validates physical dynamics nor grants actuation authority. A caller
must validate the final composed proposal through the existing supervisor and
controller-response model, after composition, before any hardware use.

## Delivery and verification

Provide an `alice-speak` CLI, WAV, sample-indexed timeline, JSON Schema, local HTML
audio/mouth/affect preview, and manifest with config, model/voice identifiers,
package versions, seed, timings and artifact checksums. Offer explicit local
speaker playback with DAC-time synchronization and mock motion output. Ordinary
tests use synthetic PCM and a fake audio device, never model downloads or devices.
Keep outputs and cached weights ignored. Ship container rendering without devices.

Test invalid plans, silence, variable segment durations, pauses, schema mismatch,
sparse expression preservation, callback latency, cancellation, underflow and
end-of-playback. Run a real CPU synthesis and an offline repeat on this box;
record measured speed without extrapolating to physical lip-sync quality.

## Alternatives

- Text-length-based jaw timing: simple but drifts from actual speech; rejected.
- Phoneme/viseme forced alignment: useful for richer mouths; deferred for Alice's
  current single opening axis.
- Streaming every generated chunk immediately: reduces startup delay but requires
  bounded buffering, underrun recovery and uncommitted-timeline revision; deferred.

## Sources

- [Requested model announcement](https://kyutai.org/blog/2026-01-13-pocket-tts/)
- [Pocket TTS source and Python API](https://github.com/kyutai-labs/pocket-tts)
- [January model configuration](https://github.com/kyutai-labs/pocket-tts/blob/main/pocket_tts/config/english_2026-01.yaml)

## Limits

RMS aperture is syllable-level motion, not phoneme-accurate articulation. Emotion
cues control the expression intent, not Pocket TTS vocal emotion. Speaker latency,
servo lag and mechanically achievable speech cadence are unmeasured. Physical
trials need an operator-approved run with the existing hardware boundary.
