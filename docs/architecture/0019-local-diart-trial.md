# ADR 0019: Local Diart qualification before speaker-aware admission

Date: 2026-09-22. Status: trial completed; live integration deferred by measured
coverage failures. The accepted live pipeline remains ADR 0018.

The operator approved trying Diart and prefers audio processing locally, with
remote compute available if necessary. Test the actual Diart online clustering
locally in an isolated environment using the existing pinned segmentation ONNX
and public WeSpeaker ONNX embeddings. Diarization is independent of Qwen ASR;
this trial does not move the existing hosted transcription endpoint.

Use session-local categories only. Do not enroll named people, store live voice
embeddings/audio/transcripts, or infer identity from the ReSpeaker's duplicated
USB channels. A speaker category is not ASR confidence or evidence of whom a turn
addresses. Unknown or delayed categories must remain explicit.

The tested local CPU path meets the measured compute budget: approximately
0.6 GiB process memory and less than 0.5 s compute per streaming update in the
synthetic screen. The quiet attended microphone timing probe also kept up. Remote
compute is therefore unnecessary for this tested diarization workload.

Category counts and returning-voice relations were correct in the small synthetic
screen, but first/new-speaker temporal coverage did not consistently meet the
predeclared 70% rule. One second of buffering improved development results but
only one of two fresh confirmation conversations passed all turn checks. Do not
activate these labels as authoritative evidence controlling live replies yet.
Preserve the tested runner and explicit limitations for a future shadow integration.

A future integration should feed a bounded worker from the existing capture,
retain anonymous labels and a short rolling heard-turn history in memory, expose
unknown/stale/multiple-speaker status, and keep current wake/overlap/Stop controls.
Align transcript spans to speaker time spans; never label a mixed utterance as one
person solely because that person spoke longest. Clear categories and context on
session restart. Freeze independent English/Cantonese and room-microphone cases
before promoting a model or delay setting. Additional compute cannot remove the
need to hear enough speech before assigning a category.

Actual test settings, negative results, provenance and reproducible commands are
in [the experiment report](../experiments/2026-09-22-diart-local.md). No live service
or decision prompt was changed by this trial.
