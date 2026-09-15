# Alice session checkpoint

**Saved 2026-09-15: ROS 2 Docker implementation review gates complete;
whole-migration review pending.** Preserve the existing worktree and artifacts:
`/home/alice/alice-workspace/.worktrees/streaming-affect-motion`
(branch `feature/streaming-affect-motion`). No merge, push or pruning performed.

- [Runbook](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/ros2.md)
  and [evidence](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/experiments/2026-09-15-ros2-runtime.md).
- Tasks 1–4 have clean implementation reviews, with Task 4 repairs at `0bb2f29`.
  Continue the coordinator's whole-migration review from baseline `3f4093b` using
  `.superpowers/sdd/2026-09-15-ros2-docker-runtime/progress.md`; do not redispatch
  completed implementation tasks. The deferred Minor is existing fork/CMake
  verification noise. All six coordinator rulings are in the ledger.
- Eight separate idle, non-root, read-only containers use an internal UDP bridge.
  Default simulation needs no devices or model cache. Offline Azelma, selected
  speaker and provisional robot preview are explicit options.
- **Live ROS hardware is unavailable** until a trusted mechanism can establish
  complete host ownership visibility for both Maestro interfaces. Enabled
  hardware overlays/actions still reject before mutation/factories. This reviewed
  coordinator amendment supersedes the earlier hardware-ready command; it adds
  no privileges or host service. Legacy bench CLI behavior is unchanged.
- Clock proof `host-monotonic-zero/v2` requires valid matching current/child
  namespace links within each participant, complete zero monotonic/boottime
  offsets and a common kernel boot. Different containers may have different
  valid namespace IDs. No timestamp translation or safety-bound widening.
- Original full verification: 1,046 passed, three skips covered by nine host
  checks, two existing fork warnings; Ruff/mypy (87 files) pass. Actual build07
  matrix passed 35 functional scenarios plus four focused supplements. Build08
  passed default/SIGTERM, public launcher and announced speaker-only checks.
- Final repaired image source SHA256:
  `c4a4a56755d81d9f507d09712b36576f7cba3b0fc926c93ac76b70d544eb8835`.
  Its 150 covering tests, seven host checks, three actual-container clock/default
  cases and no-device hidden-owner regression pass. Exact image identities and
  failed attempts are in `task4-r1-artifact-manifest.json` and its predecessor.
- Speaker-only Azelma used the selected SN6140 Pulse route and PortAudio DAC:
  185,760 samples played, zero underflows; Maestro/perception were simulated.
  This is stream evidence, not acoustic measurement or physical facial acceptance.
- Real-model admission p99 remains 21.176 ms against a 20 ms target. Coarse
  observer stop gaps reach 255–258 ms; precise self-crash marker tests measured
  about 208/209 ms to last mock receipt. None proves physical PWM cutoff.
- Active simultaneous SIGTERM can leave session handlers unquiesced. Local
  cancellation/evidence completes, followed by explicit failed cleanup after a
  five-second deadline and exit 1. The qualified rclpy workaround is version-bound.
- User-supplied `alice_description` from `codex/robot-description` (`13c2549`)
  passed Lyrical headless preview checks, retaining provisional geometry and
  `alice_preview`. No PWM bridge or added actuation scope.
- No new servo/camera trial ran. The prior visible-motion issue remains unresolved.

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
