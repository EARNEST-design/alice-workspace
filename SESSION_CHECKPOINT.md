# Alice session checkpoint

**Saved 2026-09-15: ROS 2 Docker migration design ready for review.** The user
requested the whole current pipeline run as separate nodes in the latest ROS 2
environment, packaged as a Docker project. The proposed scope is existing
speech, emotion/expression, facial control and robot-camera observation; live
microphone/LLM conversation is a separate extension unless requested.

- [Proposed ROS 2 Docker design](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/superpowers/specs/2026-09-15-ros2-docker-runtime-design.md)
- Target: ROS 2 Lyrical Luth / Ubuntu 26.04, pinned official image; eight separate
  Compose services with typed ROS interfaces and preserved audio-clock timing.
- Research verified host Docker availability and candidate Python 3.14 wheels.
  No ROS implementation, image build or hardware run has been performed.
- Next: obtain approval of the concrete design, write the implementation plan,
  then qualify Python 3.14 and implement with simulation-first tests. Continue
  in the worktree below; do not reset or remove its experiment artifacts.

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
