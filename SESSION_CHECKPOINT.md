# Alice session checkpoint

**Saved 2026-09-15: Task 4 ROS 2 Compose implementation complete, awaiting
coordinator task and whole-migration review.** Work remains in the preserved
`feature/streaming-affect-motion` worktree. No merge, push or pruning performed.

- Eight default idle non-root, read-only, capability-dropped containers run on
  an internal UDP bridge with no model cache or device requirements. Offline
  TTS, selected speaker, hardware, tools and provisional preview are explicit.
- [Runbook](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/ros2.md)
  and [dated evidence](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/experiments/2026-09-15-ros2-runtime.md)
  record commands, exact image/source/model identities and acceptance limits.
- Tasks 1–3 retain clean scoped reviews. Task 4 base is `ce7480d`; final image
  source SHA256 is `6390a6263c19b5642b60a3cd9310fe98caacbbcf5d2503b711500057f8b230c1`.
  The task report is `.superpowers/sdd/2026-09-15-ros2-docker-runtime/task-4-report.md`.
- Final full verification: 1046 passed, three skips covered by nine host tests,
  two existing fork warnings; Ruff and strict mypy (87 files) pass. Actual build
  07 matrix: 35 functional cases plus four focused crash/retirement cases pass.
  Final build 08 normal/SIGTERM, public launcher and speaker checks pass with
  separately identified evidence. All failed attempts remain preserved.
- Announced speaker-only offline Azelma completed on the SN6140 analog Pulse
  route: DAC clock, 185,760 samples played, zero underflows. Maestro/perception
  remained simulated; no new servo or camera trial ran. This is stream evidence,
  not independent acoustic measurement or physical facial acceptance.
- Real-model source-to-admission p99 remains 21.176 ms (target 20 ms), maximum
  23.221 ms. Strict 250 ms guards remain unchanged. Coarse observer stop gaps
  reach 255–258 ms and are not relabeled exact deadline proof; self-crash marker
  checks retain 207.914/209.117 ms to last mock receipt. Physical PWM cutoff is
  not proven by process death.
- Active all-node SIGTERM can leave session ROS handlers unquiesced; local
  cancel/evidence completes, then an explicit five-second failed-cleanup exit 1
  avoids destroyed-guard callbacks and indefinite Python join. This qualified
  rclpy-version workaround and failed status remain documented, not waived.
- Coordinator ruling versions clock proof as `host-monotonic-zero/v1`: same
  kernel boot plus strictly parsed zero monotonic/boottime offsets; reject bad,
  missing or nonzero metadata. Docker private namespace identity is diagnostic.
- Selective user-approved robot description from exact `13c2549` is qualified
  for Lyrical headless preview only, preserving provisional geometry and the
  `alice_preview` namespace. No PWM bridge or added actuation scope.
- Continue coordinator reviews using the existing progress ledger and artifact
  manifest. Preserve the worktree, failed attempts and unrelated hardware notes.

The following hardware checkpoint remains valid and physically unaccepted; the
ROS migration does not resolve or supersede its pending visible-motion check.

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
