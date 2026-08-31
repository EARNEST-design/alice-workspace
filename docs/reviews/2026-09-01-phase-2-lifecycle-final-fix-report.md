# Phase 2 lifecycle final-fix report

Base: `09085ea`

This was an offline implementation and verification pass. It did not enumerate
or access the camera, TTY, Maestro controller, or actuators.

## Durable staged state

`RunStatus.STAGED` is now the first durable success state for hardware motion.
The private deterministic core receives an explicit hardware-staging flag and
writes a staged manifest directly; it never writes a completed manifest and
then rewrites or removes it. The staged directory also contains the typed
`pending_power_removal` draft. Analyzer input requires `COMPLETED` and
independently requires shutdown/power-removal provenance for hardware-capable
runs.

`ArtifactManifest` rejects:

- staged non-hardware or unprovenanced runs;
- staged runs already carrying shutdown provenance; and
- completed hardware runs without typed Home, cleanup, output-identity, and
  power-removal provenance.

Regression evidence inspects the durable staged directory after execution and
proves its manifest status is `staged` and no completed status was written.

## Output reservation

Execution validates and reserves the exact output destination before observer
construction, supervisor arming, watchdog start, or motion. Existing, racing,
or unusable destinations fail before commands and trigger explicit revoke and
close cleanup. Only a non-reversible output-identity hash is stored in the
reservation, pending draft, and final shutdown provenance; broad filesystem
paths are not serialized.

Final and aborted generations are built in a durable sibling stage and renamed
atomically to the reserved destination. The reservation is released only after
successful publication.

## Retryable pending capability and terminalization

Pending handles are checked for token, exact object, issuing PID, fork state,
draft integrity, and expiry without being consumed. Invalid/stale/binding
confirmation leaves the handle intact. Atomic-publication failure also leaves
the reservation, staged evidence, timer, and capability intact for retry.
Successful publication is the point at which the registry entry is consumed.

A configured expiry timer and weakref abandonment terminalize unfinished runs
as sanitized `ABORTED` evidence with `power_removal_required=true` and
`completion_eligible=false`, then remove staged data and authority. Explicit
`abandon_pending_hardware_identification` uses the same terminal path. Forked
children detach the pending registry and cannot exercise inherited authority.

## CLI cleanup

The installed `alice-hardware-run` CLI now explicitly:

- cancels the prepared handle after power-enable mismatch, EOF, or interrupt;
- abandons the pending handle after power-removal mismatch, EOF, or interrupt;
- never relies on CPython object finalization for these interactive exits; and
- still creates wall and monotonic confirmations only after the matching
  operator input.

## Verification

- Full suite: `348 passed`; two existing CPython multi-threaded `fork()`
  deprecation warnings.
- Ruff: clean.
- Strict mypy: clean across 39 source files.
- `git diff --check`: clean.
- Wheel and source distribution: built successfully.
- Isolated installed-wheel entry-point/resource smoke test: passed.

## Residual constraints

- The repository hardware configuration intentionally remains non-executable
  until run-specific approval, electrical evidence, enable token, and model path
  placeholders are supplied outside version control.
- The watchdog remains process-local; the operator-controlled servo-power
  boundary is authoritative.
- Abandonment publication retries in a daemon timer after a transient storage
  failure. Persistent storage failure can prevent durable terminal evidence,
  but cannot restore motion authority or produce a completed run.
