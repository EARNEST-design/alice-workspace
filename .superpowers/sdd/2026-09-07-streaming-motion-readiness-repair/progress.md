# SDD ledger — plan: docs/superpowers/plans/2026-09-07-streaming-motion-readiness-repair.md

## Preflight scan

| Tasks | Shared file/interface | Finding |
|---|---|---|
| 1 → 2 | `ProductionCandidateComposer`, head-event commands, accepted-prefix state | Ordered intentionally: establish feasible repeated replanning before expanding valid head start poses. |
| 1 → 3 | controller response runtime and package tests | Clean: Task 1 changes runtime use; Task 3 changes exact configuration identity, not solver behavior. |
| 1–3 → 4 | loaded package/composer and deterministic replay | Clean: readiness must consume the repaired production path, so it is last. |
| 2 → 4 | resumable head gestures | Clean: readiness replay exercises the finalized primitive behavior. |
| 3 → 4 | `response_sha256` and package identities | Clean: readiness reports the exact response identity introduced by Task 3. |
| 1 | Tests/files/implementation agree | Clean; explicit fallback must preserve pre-attempt state and remain controller-feasible. |
| 2 | Tests/files/implementation agree | Clean; ordinary gestures preserve inactive axes, while `return_to_attention` owns all head axes. |
| 3 | Tests/files/implementation agree | Clean; response identity is distinct from firmware-settings identity. |
| 4 | Tests/files/implementation agree | Clean; discovery is metadata-only and the CLI cannot open serial/camera or command targets. |

Ruling: the repair work is a new plan after the prior plan exhausted its final-review fix wave — preserves that review history while allowing evidence-driven correction — cost if wrong: review artifacts are split across two SDD workspaces.
Ruling: hardware readiness means a passing read-only device identity check plus a long mock/controller-response replay and reviewed opt-in procedure; it does not include energizing servos — respects the user's master-switch-off state and repository approval invariant — cost if wrong: one explicit operator-approved hardware turn remains after software completion.

Task 1: complete (commits 2879162..9dbdbf1, review clean; 25-prefix replay and 110,000-state adversarial feasibility probe pass)
Task 2: complete (commits 9dbdbf1..66a18df, review clean; all gesture kinds/orderings and production residual-to-gesture integration pass)
Task 3: complete (commits 66a18df..9fe1eca, review clean; exact response identity and fail-closed v2 package/training records)

Task 4: complete (fix round 1 remediation verifies O_PATH-first regular-file inspection, exclusive inode-protected report publication, canonical kernel/USB device identity, immutable package-byte snapshots, and sanitized stable check/publication codes; 618-test/full static verification and metadata-only connected checks recorded in task-4-report.md)
