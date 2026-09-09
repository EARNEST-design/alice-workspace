# Agent operating guide

This is a safety-relevant robotics and ML repository. Work from evidence, keep changes reviewable, and record decisions.

## Invariants

- For Alice's attended bench tests, an explicit user request to run authorizes motion within the stated actuator scope. The operator has declared the small robot connected, powered and ready, with a hand at the master switch; do not repeat readiness checklists, typed confirmation phrases, or routine power-off acknowledgments. Keep automatic controller identity/error checks, calibrated motion limits, and fault/cancel stops. Ask only when a concrete new issue requires an operator decision.
- Default hardware adapters to dry-run/mocked operation.
- Never commit credentials, personal participant data, raw biometric data, or proprietary datasets.
- Track data provenance, consent constraints, model versions, evaluation splits, and experiment seeds.
- Keep perception, decision/policy, and actuation behind explicit interfaces.
- Add tests before changing behavior; verify claims with commands and captured results.
- Treat `venetanji/alice-feedback` and other shared repositories as references unless reuse is explicitly approved and license-compatible.

## Coordination

- Put durable decisions in `docs/architecture/` as ADRs.
- Put hardware facts and unknowns in `hardware/`.
- Give each experiment a config, metrics, artifacts manifest, and short conclusion.
- Specialized roles live in `agents/`; invoke the narrowest relevant role for a task.


## Session continuation

- Read `SESSION_CHECKPOINT.md` for the latest accepted speech hardware baseline and the next speech/emotion integration plan before resuming this work. Preserve the existing worktree and local experiment artifacts.
