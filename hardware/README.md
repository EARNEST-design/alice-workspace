# Hardware

This directory holds versioned inventory, wiring diagrams, datasheet links, safe limits, and non-secret calibration templates. Machine-specific calibration belongs under ignored `hardware/calibration/local/` until a reviewed format and safe publication policy exist.

## Current controller facts

Read-only inventory identifies a Pololu Mini Maestro 12, serial `00037376`, with stable interface-00 and interface-02 paths recorded in `docs/discovery/hardware-inventory.md`. The functional command-port role of interface 00 is not yet verified. Do not infer it from numbering.

The proposed, non-approved Phase 2 procedure is in `bringup/phase-2-actuator-identification.md`. Its repository configuration deliberately contains invalid `REQUIRED_...` values. The verifier is dry-run only; hardware execution remains blocked on the procedure's unresolved preflight items and separate operator approval.
