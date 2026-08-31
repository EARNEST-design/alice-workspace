# Phase 2 final review fix report

Scope: final whole-branch findings against base `db2f0af`. This pass was
hardware-free: it did not enumerate, open, or access camera, TTY, Maestro, or
actuator devices.

## Finding 1 — one versioned variance definition

Addressed in `src/alice/analysis/system_identification.py` and the existing
runner. All variance and noise calculations now use population variance
`population-ddof0/v1` (`mean((x - mean(x))^2)`, divisor `N`, `ddof=0`). The SNR
formula is revisioned as `session-effect-rss-population/v2`. A real-format
artifact round trip verifies `[0.1, 0.2, 0.4]` as
`0.015555555555555557`, including the recorded visual-settling value.

Evidence: `test_all_reported_variance_uses_versioned_population_definition`,
`test_real_artifact_round_trip_accepts_population_variance_semantics`, and
`test_snr_uses_session_effects_and_population_rss`.

## Finding 2 — trusted production perception composition and CLI

Addressed by `ProductionIdentificationObserver` in
`src/alice/perception/identification_observer.py`. It owns the exact stable C525
selector and a hash-validated MediaPipe adapter, reads one frame per
observation, exposes actual negotiated settings and runtime provenance, and
closes detector and camera deterministically. The public hardware execution
root constructs it internally and has no observer/camera/detector/factory
argument.

`alice-hardware-run` is an installed, interactive prepare → power-enable →
execute → power-removal → finalize CLI. Repository placeholders stop before
hardware preparation; default mode is verifier-only. Confirmation wall and
monotonic timestamps are created only after the matching operator input.

Evidence: `tests/perception/test_identification_observer.py`,
`tests/experiments/test_hardware_run_cli.py`, public-signature assertions, and
the installed-wheel smoke test.

## Finding 3 — completion and power-removal lifecycle

Addressed in `src/alice/experiments/hardware_identification.py` and typed
manifest provenance. Execution returns an opaque, exact-object/PID/fork-bound,
single-use `PendingPowerRemovalHandle`. The hidden durable stage contains a
typed `pending_power_removal` draft, not a completed manifest. It is issued only
after final independently verified Home, accepted supervisor completion,
watchdog termination, Maestro close, and observer close.

`finalize_hardware_identification` requires a fresh operator-created
`PowerRemovalConfirmation` bound to the run, power-enable challenge, exact raw
config and manifest hashes, and draft hash. Only then does one atomic generation
publication expose `COMPLETED` with typed shutdown and power-removal provenance.
Cleanup uncertainty publishes sanitized `ABORTED` evidence with
`power_removal_required=true` and `completion_eligible=false`; it returns no
pending handle and cannot be upgraded. Tokens and free-form secret text are not
serialized.

Evidence: staged-publication/finalization, single-use, cleanup-failure,
watchdog-stop, fork, forged-handle, and secret-sanitization tests in
`tests/hardware/test_hardware_identification_entry.py`.

## Finding 4 — monotonicity, saturation, and asymmetry

Addressed with typed named matrices, named formulas, formula revisions,
serialization, analysis configuration, conclusion availability states, and
analyzer revision `system-identification/v2`.

- Monotonicity: signed Home-to-positive/negative consistency in `[0, 1]`.
- Asymmetry: absolute difference in positive and negative one-sided slope
  magnitudes, with typed uncertainty state.
- Saturation: `1 - |outer incremental slope| / |inner Home-to-offset slope|`.
  The initial single-magnitude protocol reports typed missing/inconclusive;
  a multi-magnitude fixture verifies slope compression numerically.

Evidence: the monotonicity/asymmetry/single-magnitude test, multi-magnitude
saturation test, publication tests, and packaged conclusion template.

## Threat-model clarification

The ADR, bring-up procedure, implementation plan, and low-level Maestro class
now state that trusted composition prevents accidental/in-scope experiment
bypass; it is not protection against arbitrary malicious Python in the same
process. The reviewed process/OS environment and operator-controlled servo
power boundary remain authoritative.

## Verification

- Full suite: `340 passed`; two existing CPython multi-threaded `fork()`
  deprecation warnings.
- Ruff: clean.
- Strict mypy: clean across 39 source files.
- `git diff --check`: clean.
- Wheel and sdist build: successful.
- Isolated installed-wheel smoke, including `alice-hardware-run`: passed.

## Residual concerns

- The repository hardware configuration intentionally remains non-executable:
  run ID, approval, enable token, electrical evidence, and pinned model path are
  placeholders. This is a safety gate, not missing implementation.
- C525 focus and exposure remain explicitly unavailable in the reviewed
  template. Runtime values are still recorded; any reviewed available setting
  must match exactly.
- The watchdog is process-local and cannot survive kernel/process failure.
  Operator power removal remains the authoritative safety action.
