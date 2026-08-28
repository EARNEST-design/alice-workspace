# Agent operating guide

This is a safety-relevant robotics and ML repository. Work from evidence, keep changes reviewable, and record decisions.

## Invariants

- Never command physical actuators without an explicit, reviewed bring-up procedure and user approval.
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

