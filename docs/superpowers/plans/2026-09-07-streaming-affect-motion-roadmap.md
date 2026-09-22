# Streaming Affect Motion Roadmap

For the next session after the Tasks 1–5 streaming review, start with
[the coordinated-motion handoff](2026-09-07-ml-architecture-next-session.md)
and [ADR 0005](../../architecture/0005-coordinated-streaming-motion-learning.md).
That handoff prioritizes review repairs and an integrated baseline, followed by
data/evaluation and a small ACT-style challenger. It extends the sequence below.

The approved design is split into independently testable plans:

1. `2026-09-07-affect-motion-foundations.md` — integrate the existing Phase 2
   branch, define contracts, model controller interpolation, and ship the anchor
   plus procedural-variation baseline.
2. `2026-09-07-stateful-face-head-generation.md` — implement the streaming
   state-space residual, explicit face events, and semantic neck gestures.
3. `2026-09-07-affect-motion-data-evaluation.md` — build provenance-aware
   datasets, replay metrics, and the lightweight lab-rating workflow.
4. `2026-09-07-alternative-motion-models.md` — run evidence-gated transformer,
   diffusion, and fully learned neck experiments only after the selected core
   baseline exposes a measurable limitation.

Execute plans 1–3 in order. Plan 4 is a research menu, not a prerequisite for a
working generator. Within each plan, disposable spikes use compact records;
decision-grade comparisons use immutable splits, artifacts, and conclusions.

The existing Phase 1 and Phase 2 worktrees are prerequisites, not code to
reimplement. Review and integrate `feature/phase-2-actuator-identification`
before Plan 1; that branch already contains the Phase 1 contracts and capture
pipeline. No plan authorizes actuator commands. Hardware trials retain a short,
explicit operator-approved enable boundary.
