# Hardware

This directory holds versioned inventory, wiring diagrams, datasheet links, safe limits, and non-secret calibration templates. Machine-specific calibration belongs under ignored `hardware/calibration/local/` until a reviewed format and safe publication policy exist.

## Current controller facts

Read-only inventory identifies a Pololu Mini Maestro 12, serial `00037376`, with stable interface-00 and interface-02 paths recorded in `docs/discovery/hardware-inventory.md`. Official Pololu Linux port ordering plus the observed stable-interface mapping identifies interface 00 as this unit's Command Port; the evidence and its limitations are recorded in `maestro-command-port-evidence-2026-09-01.md`. A fresh identity check and operator attestation remain mandatory before every run.

The proposed, non-approved Phase 2 procedure is in `bringup/phase-2-actuator-identification.md`. Its repository configuration deliberately contains invalid `REQUIRED_...` values, including unresolved structured electrical-evidence path/hash fields. The verifier is dry-run only. Hardware preparation and power-enabled execution are separate, single-use stages; both remain blocked on the procedure's unresolved preflight items and separate operator approval.
