# Mouth-only speech trial implementation plan

**Goal:** Run the selected Azelma clip on the actual jaw after a concrete reviewed
procedure and explicit operator run request; ordinary invocations remain mock.

**Spec:** `docs/architecture/0008-mouth-only-speech-trials.md`.

**Architecture:** A pure discrete trajectory limiter feeds the existing
supervisor and settled-output adapter. The audio producer only updates a latest
sample slot. A separate trusted CLI owns devices, checkpoints and evidence.

**Tech stack:** Python, existing Pydantic contracts, sounddevice/PortAudio,
pyserial/Maestro, pytest; no new perception model or training dependency.

## Tasks

- [x] Write tests for `next_jaw_target(position, velocity, desired, elapsed_s,
  config)`: bounded startup/reversal/exit; exact Home; nonfinite/gap rejection.
  Implement in `src/alice/speech/jaw_trial.py` after the tests fail.
- [x] Add a jaw-only adapter boundary and single-command executor. Test actual
  supervisor/permit behavior, delayed APPLIED time, faults, stale audio, and
  rejection of every non-jaw target. Preserve settled-controller semantics.
- [x] Add immutable verified speech-artifact loading. Tests corrupt checksums,
  malformed timing and wrong PCM before allowing any device creation.
- [x] Implement a trusted `alice-jaw-trial` entry point. Mock by default;
  explicitly gated device reading and standing attended bench setup. Audio and
  servo work remain independent. Tests exercise explicit enablement, cancellation and fault cleanup and
  verify no unexpected motor write. Keep hardware factories out of CLI inputs.
- [x] Write the executable bring-up procedure and exact trial configuration;
  prepare a real-audio/mock-servo trial with measured dispatch timing. Obtain
  independent code/procedure review and run relevant tests, lint and typing.
- [x] Run under the operator's explicit request and standing setup declaration.
  Only then run device preflight and the short physical trial, stop on faults,
  and record results plus operator observations without claiming unmeasured sync.

Do not replace fresh operator observations with generated approval assertions.
Do not command serial devices while implementing or running ordinary tests.
