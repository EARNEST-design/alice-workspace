# Incremental speech and expression software checkpoint

Saved 2026-09-10 in the preserved `feature/streaming-affect-motion` worktree.
Software revision: **83f6b1d4194e93fc43fe46019ae45d9af547aecc**.

Tasks 1–5 of the [integration plan](../superpowers/plans/2026-09-09-speech-emotion-integration-next-session.md)
are implemented and qualified with **845 passing tests**, Ruff, mypy (80 files),
independent review, real incremental Azelma synthesis, and actual speaker output.
Task 6's selected facial executor and physical combined-motion trial remain next.

Read the [experiment report](../experiments/2026-09-09-speech-emotion-integration.md)
and [ADR 0010](../architecture/0010-live-speech-composition-and-fault-boundaries.md)
for measurements, limitations, interfaces and fixes. The software first emits
PCM before synthesis completes; event barriers qualify playback before both TTS
and the fixture text source finish. A live provider-specific LLM adapter was not
added. Authored expressions are explicitly distinguished from learned fallback.

The qualified run is
`artifacts/speech/integration-2026-09-10/azelma-speakers-qualified/`.
Its `preview.html` is self-contained. `manifest.json`, `metrics.json`,
`composed.jsonl`, `chunks.json`, `generated.wav`, `expression-state.json`, and
`proposal-derivatives.json` retain the evidence. First DAC audio: 3.120 s cold,
before first-clause final PCM at 3.361 s; 7.74 s audio, zero underflows. The warm
worker comparison gave identical PCM with first chunk in 88.5 ms.

Do not drive the raw proposed targets directly. Their finite differences exceed
hardware command caps; Task 6 must compose through trusted per-channel bounds.
Candidate initial scope: channels 3/4 eyelids, 5 forehead, 6 mouth, 9/11 corners.
Eye gaze 8/10 and head/neck 0/1/2 are separate opt-ins, absent from that scope.

The last accepted **physical** speech baseline is still the
[September 9 mouth trial](2026-09-09-speech-motion.md): Azelma, calibrated jaw
range 4608–5440 around Home 5059, runtime speed/acceleration 0/0, and 100 ms lead.
Normal Home completion restores jaw runtime 0/11; fault closure sends no restore
transactions. No servos or cameras were opened during this software session.
Preserve all earlier local audio/camera artifacts and these newer experiments.

Next work starts at Task 6. Rebaseline the current worktree and preserve anything
newer; do not repeat the completed ML repairs or reset to either checkpoint.
The operator's standing attended-run authorization policy remains unchanged.
