# Alice session checkpoint

**Saved 2026-09-10: incremental speech and expression software qualified.**
Tasks 1–5 are implemented at `83f6b1d`, with 845 passing tests and a real Azelma
speaker run. Next is Task 6: trusted selected-face execution and an attended trial.

Continue in the preserved worktree:
`/home/alice/alice-workspace/.worktrees/streaming-affect-motion`
(branch `feature/streaming-affect-motion`).

- [Latest software checkpoint](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/checkpoints/2026-09-10-incremental-speech-emotion.md)
- [Accepted physical settings](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/checkpoints/2026-09-09-speech-motion.md)
- [Next-session implementation plan](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/superpowers/plans/2026-09-09-speech-emotion-integration-next-session.md)

Accepted baseline: Azelma, full calibrated mouth range, runtime jaw speed and
acceleration 0/0, and **100 ms mouth lead**. The operator said “Looks aligned”.
Local audio/camera/metrics artifacts remain in the worktree. Incremental TTS,
DAC-clock speech composition and authored expression replay are qualified.
Learned mode remains neutral fallback; no fitted package was found. Combined
physical facial expressions are not yet implemented or accepted.

Use `git log -1 --format='%h %s' -- SESSION_CHECKPOINT.md` inside that worktree
to identify the checkpoint commit. Inspect and preserve any newer changes.
