# Alice session checkpoint

**Saved 2026-09-15: ROS 2 Docker migration approved; implementation in progress.** The user
requested the whole current pipeline run as separate nodes in the latest ROS 2
environment, packaged as a Docker project. The proposed scope is existing
speech, emotion/expression, facial control and robot-camera observation; live
microphone/LLM conversation is a separate extension unless requested.

- [Approved ROS 2 Docker design](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/superpowers/specs/2026-09-15-ros2-docker-runtime-design.md)
- Target: ROS 2 Lyrical Luth / Ubuntu 26.04, pinned official image; eight separate
  Compose services with typed ROS interfaces and preserved audio-clock timing.
- Python 3.14 qualification built the pinned Lyrical core, speech, perception
  and test images. Real offline Azelma synthesis and MediaPipe model opening
  passed with no devices or runtime network. No new hardware trial has run.
- [Implementation plan](/home/alice/alice-workspace/.worktrees/streaming-affect-motion/docs/superpowers/plans/2026-09-15-ros2-docker-runtime.md)
- Task 1 is complete at `dd84f2c`, with clean review and explicit reviewed
  build inputs. Task 2 (typed ROS interfaces and transport guards) is next. The Lyrical
  regression run passed 881 tests with one optional Node.js preview skip; the
  original host baseline passed 879 tests. Follow the task ledger in
  `.superpowers/sdd/2026-09-15-ros2-docker-runtime/progress.md` inside the
  preserved worktree; do not redispatch completed tasks or clear artifacts.

User identified the existing `alice_description` on `codex/robot-description`
(commit `13c2549`). Task 4 will selectively reuse it as an optional Lyrical
visualization profile, preserving provisional geometry and `/alice_preview`.
It does not replace servo calibration or add a PWM-to-joint conversion.

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
