# Alice Robotics Workspace

Greenfield, agent-driven workspace for the Alice social-robotics platform.

The immediate objective is to build a reproducible pipeline joining facial landmarks, emotion inference, and safe motor actuation. The repository is intentionally new: the earlier `venetanji/alice-feedback` project is a reference, not a codebase to fork or copy.

## Current status

- English/Cantonese live conversation now works through ReSpeaker USB, hosted
  Qwen ASR, remote Qwen replies and accepted local female voices. The current
  admission backend is temporarily Qwen; a smaller dedicated model is the next
  evaluation priority. See [the conversation runbook](docs/conversation-bench.md).
- Resume with the [September 22 handoff](docs/checkpoints/2026-09-22-conversation-face-handoff.md)
  and [speech/face plan](docs/superpowers/plans/2026-09-22-conversation-face-integration.md).
  Synchronized mouth/face and ROS foundations exist separately; the live
  conversation-to-servos connection and physical expression acceptance are next.
- Phase 2 full-range actuator characterization and initial live mimicry bring-up
  were completed on 2026-09-02; see
  `docs/experiments/2026-09-02-mimicry-bringup.md`.
- Detected controller: Pololu Mini Maestro 12-Channel USB Servo Controller (`1ffb:008a`, serial `00037376`).
- Expected ports: `/dev/ttyACM0` and `/dev/ttyACM1`; use stable `/dev/serial/by-id/...` paths in configuration.
- Semantic channel wiring and conservative software limits are verified in
  `hardware/alice-face-v1.yaml`; downstream motor-driver make remains unknown.
- Reference repository and historical Alice research are documented under `docs/discovery/`.

## Safety gate

Do not energize or move hardware until the wiring map, mechanical travel limits, emergency stop, current limits, and a low-power bench procedure are documented and reviewed. Development should default to recorded data, mocks, and simulation.

## Layout

Local CPU speech, audio-aligned mouth proposals and future LLM affect cues are
available through `alice-speak`. See [the speech runbook](docs/speech.md) and
`config/speech/alice-introduction.json` for a playable hardware-free example.

- `agents/`: specialized agent charters
- `docs/discovery/`: evidence and open questions
- `docs/architecture/`: decisions and system design
- `hardware/`: inventory, wiring, calibration, and safe bring-up
- `infra/`: host bootstrap, machine profile, udev policy, and future Compose topology
- `models/`: model cards, experiments, and evaluation definitions
- `src/`: production packages (added after architecture decisions)
- `tests/`: automated tests and hardware-free contract tests

## Next decisions

1. Qualify a small speak/wait model using the [dedicated admission plan](docs/superpowers/plans/2026-09-22-small-speech-admission.md).
2. Photograph/identify the motor drivers and trace their connections to the Maestro.
3. Share any additional repositories and datasets before choosing interfaces or ML frameworks.
4. Define measurable success criteria for face mesh, emotion inference, and motor expression.
