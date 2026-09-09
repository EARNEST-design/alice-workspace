# Streaming motion repair and baseline qualification — 2026-09-09

## Outcome and revision

Tasks 1–5 of the September 7 ML architecture handoff are reconciled and repaired.
Task 6 qualifies a configured synthetic baseline and records the next state/design
boundaries in [ADR 0006](../architecture/0006-streaming-continuation-and-response-provenance.md).
This is software evidence, not hardware accuracy, believable motion, or learned
affect responsiveness.

Worktree: `/home/alice/alice-workspace/.worktrees/streaming-affect-motion`, branch
`feature/streaming-affect-motion`. Starting HEAD was clean
`b42e27399d1d8a6972ce7a793d61a186ce346b47`, already ahead of the handoff snapshot.
The verified implementation HEAD is
**`05178d183f490d8dcbd6d44fe5cac5e63b9e76eb`**. The final repository HEAD and exact
remaining dirty paths are captured in the retained artifact manifest, after the
separate documentation commit. No hardware was commanded and no data collected.

Owned implementation commits:

- `d256d11` — preserve sparse streaming continuation.
- `23eb8d5` — preserve accepted events and pose-bound head recovery.
- `23e214b` — identify training response surrogate in model packages.
- `05178d1` — qualify composed streaming synthetic baseline.

## Finding dispositions

| Finding | Disposition |
| --- | --- |
| A: timers restart | Reproduced, then fixed with absolute event/drift/RNG continuation and a 60-second eye-motion regression. |
| B: last sparse delta becomes whole pose | Reproduced, then fixed at adapter and acceptance boundaries; mouth/neck deltas and unchanged channels survive JSON restoration. |
| C: fallback creates/drops events | Reproduced, then fixed: suppress new events, complete accepted phases, cancel futures, and preserve rollback/restart behavior. |
| D: head recovery from drift | Original crash was already repaired; ordinary active-axis recovery now returns to its captured pose, with explicit RETURN distinct. |
| E: incompatible package components | Existing repair retained; added normally validated extra-head-dimension and unknown-face-actuator cases. |
| F: nonfinite weights | Existing save/load rejection retained; failed saves explicitly leave no destination. |
| G: response identity conflation | Current arithmetic now has its own bounded Euler identity and resolved parameters; v3 package admission binds them independently of the runtime reference. |

V1/v2 training records/packages are rejected rather than silently relabeled.
Incompatible legacy head history is also rejected. The procedural adapter retains
its existing neutral fallback while its absolute clock advances; explicit-event
completion is a production-composer guarantee. Cross-revision state migration is
not claimed.

## Qualification results

Real production components use checked-in configurations and an explicitly
zero-weight residual. Model initialization seed is 29; runtime seeds are 7 and
29. Each run lasts 60 virtual seconds: 150 accepted 0.4-second prefixes from
1-second lookahead, with actual command cadence 5 Hz. The whole accepted state
and every prefix match a separately initialized, repeatedly restored replay.

| Seed | Blinks with accepted motion | Gaze events with accepted motion | Head events | Fallback prefixes |
| --- | --- | --- | --- | --- |
| 7 | 10 | 6 | 2 | 41 / 150 |
| 29 | 5 | 7 | 3 | 65 / 150 |

The transition fixture changes supported intent at 3.2 seconds during a LOOK_UP
started at 2.8 seconds and retains its absolute phase through recovery. Existing
production tests cover stale input during a blink and rejected lookahead. Static
face output and a discontinuous boundary fail the qualification checks.

| Seed | Group | Max position jump | Max velocity change (/s) | Max acceleration change (/s²) |
| --- | --- | --- | --- | --- |
| 7 | Face | 0 | 0.882637 | 5.514340 |
| 7 | Head | 0 | 0.127672 | 1.428184 |
| 29 | Face | 0 | 0.777875 | 4.897906 |
| 29 | Head | 0 | 0.170983 | 1.619662 |

These are normalized command positions and adjacent one-sided finite-difference
changes at 0.2-second spacing, not measured physical derivatives. Full formulas,
per-channel ranges, configuration/weight hashes, and replay digests are retained
in the metrics artifact. Fallback remains substantial. The selected steady seeds
move pitch but leave yaw/tilt static; empty affect-to-anchor mappings and zero
residual weights mean these fixtures do not establish affect-expression quality.

## Final quality gate

A Git archive of `05178d1` was checked with its own `uv sync --locked`
environment: Python 3.13.15, NumPy 2.5.2, Torch 2.14.0+cpu, Pydantic 2.13.5.
The lockfile SHA-256 is
`ce3e41b3cf9491390e0d2484694044d542966302540bfb9da96c379c55a40aff`.

- Full pytest: **687 passed**, two existing `os.fork()` deprecation warnings.
- Ruff: passed. The archive sits beneath ignored scratch storage, so the final
  command uses `ruff check --no-respect-gitignore src tests`; a first invocation
  skipped ignored files and was not counted as validation.
- Mypy: passed for **60 source files**.
- `git diff --check b42e273 05178d1`: passed.
- Focused repairs had captured failing regressions before production edits;
  Tasks 2, 3, 5 and 6 passed independent spec/quality reviews with no findings.
  The overall session review is retained with the artifacts.

## Retained evidence and continuation

Artifacts are in the ignored `artifacts/streaming-repair-2026-09-09/` directory.
The [manifest](../../artifacts/streaming-repair-2026-09-09/manifest.json) records
all retained artifact checksums, source/final revisions, commands, and dirty paths.

| Artifact | SHA-256 |
| --- | --- |
| [Full test log](../../artifacts/streaming-repair-2026-09-09/full-suite.log) | `ef15f1795d1c841c7bcb30531a4829aced31ba7ce4366e3d09e77054085944d7` |
| [JUnit evidence](../../artifacts/streaming-repair-2026-09-09/full-suite.xml) | `d4b668c4f7b42b729a4c1fd9a182156ee88af70346391ae6c57c12061fa4ce62` |
| [Derived metrics](../../artifacts/streaming-repair-2026-09-09/qualification-metrics.json) | `66c8f57176d270a5bf43beeaec43faa041d92afac69c166b28704c76d8095f7f` |

Concurrent speech source/config/tests, dependency edits, container files, and
speech documentation are preserved and excluded from these commits. The main
checkout's existing uncommitted handoff/architecture documents are also untouched.
Final checks certify the frozen motion revision, not the changing shared tree.

Next unchecked work is Milestone A: design explicit behavioral context/persistent
style and a versioned continuation/observation contract, including composition
algorithm identity. Follow the existing
[data/evaluation plan](../superpowers/plans/2026-09-07-affect-motion-data-evaluation.md)
for synchronized episodes and grouped splits. A reference-equivalent tensor
backend and measured mechanical correction remain separate scoped experiments;
new model families and hardware collection have not been started.
