# Alice session checkpoint

**Saved 2026-09-11: Task 6 software qualified; latest facial tuning awaits a
visible-motion trial.** 879 tests pass. Combined speech/face execution is
implemented; the latest full-blink/final-sad-pose run changed controller PWM but
the camera showed almost no movement. The operator has been asked whether servo
power is still on. Do not infer physical acceptance from PWM readback.

Continue in the preserved worktree:
`/home/alice/alice-workspace/.worktrees/streaming-affect-motion`
(branch `feature/streaming-affect-motion`). Preserve all local artifacts.

- [Current checkpoint](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/checkpoints/2026-09-11-selected-face-speech.md)
- [Experiment and feedback](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/experiments/2026-09-11-selected-face-speech.md)
- [Integration plan](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/superpowers/plans/2026-09-09-speech-emotion-integration-next-session.md)

Accepted mouth baseline remains Azelma, full calibrated range, runtime jaw 0/0
and 100 ms lead. The user requested quicker mouth response, fuller blinking and a
closed-mouth frown for sadness. Candidate tuning and the exact next-run command
are in the current checkpoint. Learned mode remains unfitted neutral fallback;
no live LLM provider is claimed.

Use `git log -1 --format='%h %s' -- SESSION_CHECKPOINT.md` in the preserved
worktree to identify the latest checkpoint commit. Inspect newer changes.
