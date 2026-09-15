# Alice session checkpoint

**Saved 2026-09-15: ROS 2 Docker implementation and final review complete;
live ROS hardware and physical acceptance remain unavailable/pending.** Preserve the existing worktree and artifacts:
`/home/alice/alice-workspace/.worktrees/streaming-affect-motion`
(branch `feature/streaming-affect-motion`). No merge, push or pruning performed.

- [Runbook](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/ros2.md)
  and [evidence](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/experiments/2026-09-15-ros2-runtime.md).
- Tasks 1–4 have clean implementation reviews. Whole-migration review found
  three Important issues and one documentation correction, fixed at `5ed4e23`:
  accepted action cancellation/fault semantics, first DAC progress expiry, and
  production verification/evidence of loaded model assets. The sole scoped
  re-review closed all four findings with no new breakage. Do not redispatch
  completed work. Existing fork/CMake verification noise is non-blocking debt.
  The review ledger, reports and diffs are archived under
  `artifacts/ros2/2026-09-15/sdd-final-review/`, with a verified archive manifest.
  All six coordinator rulings are also in `coordinator-final-rulings.md` beside
  that directory. Only the redundant plan scratch copy was removed.
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
- Final verification: 1,079 passed, three skips covered by nine host checks,
  two existing fork warnings; Ruff/mypy (87 files) pass. Eight targeted actual
  container outcomes pass across two retained attempts: default, cancellation
  during preparation/playback/finalization, first DAC loss, model mismatch,
  stalled-TTS cancellation and real offline Azelma. The first offline helper
  failed before inference; only that helper was corrected and that case rerun.
- Final repaired image source SHA256:
  `f7b99195cbd2ae7d72a7f097f88e549ef47654abd268a52db60045f38efa573a`.
  Exact role image IDs, commands and 1,927 hashed evidence files are bound in
  `artifacts/ros2/2026-09-15/final-fix-artifact-manifest.json`. Production TTS
  verifies model/tokenizer/Azelma bytes before loading, binds evidence to the
  loaded worker lifetime and rejects silent reload after worker death.
- Earlier evidence retains its own identities: build07 passed 35 functional
  cases plus four supplements; build08 passed 1,046 tests, default/SIGTERM,
  public launcher and announced speaker-only checks. The subsequent clock/owner
  repair passed 150 covering tests, seven host checks and three container cases.
- Speaker-only Azelma used the selected SN6140 Pulse route and PortAudio DAC:
  185,760 samples played, zero underflows; Maestro/perception were simulated.
  This is stream evidence, not acoustic measurement or physical facial acceptance.
- Latest real-model run with simulated DAC: 185,760 samples played with zero
  underflows, p99 admission 16.965 ms and maximum 21.683 ms. Earlier p99 runs
  were 21–22 ms against the 20 ms target; repeatable timing acceptance remains
  open. First DAC expiry uses the 250 ms predicate; the watchdog observed the
  injected failure after 259.033 ms. Coarse observer stop gaps reach 255–258 ms;
  precise self-crash marker tests measured about 208/209 ms to last mock receipt.
  These measurements do not prove a general exact 250 ms stop or physical PWM cutoff.
- Active simultaneous SIGTERM can leave session handlers unquiesced. Local
  cancellation/evidence completes, followed by explicit failed cleanup after a
  five-second deadline and exit 1. The qualified rclpy workaround is version-bound.
- User-supplied `alice_description` from `codex/robot-description` (`13c2549`)
  passed Lyrical headless preview checks, retaining provisional geometry and
  `alice_preview`. No PWM bridge or added actuation scope.
- No new servo/camera trial ran. The prior visible-motion issue remains unresolved.
- Next work is a separately designed and qualified complete host-ownership
  mechanism before enabling ROS hardware, plus repeatable timing and physical
  acceptance. Simulation, offline TTS, speaker overlay and provisional preview
  are available through the runbook. Preserve legacy calibration and bench CLI.

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
