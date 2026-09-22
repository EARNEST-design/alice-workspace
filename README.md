# Alice Robotics Workspace

Greenfield, agent-driven workspace for the Alice social-robotics platform.

The immediate objective is to build a reproducible pipeline joining facial landmarks, emotion inference, and safe motor actuation. The repository is intentionally new: the earlier `venetanji/alice-feedback` project is a reference, not a codebase to fork or copy.

## Current status

The current implementation is on
[`feature/streaming-affect-motion`](https://github.com/EARNEST-design/alice-workspace/tree/feature/streaming-affect-motion).
Start with the [September 22 handoff](https://github.com/EARNEST-design/alice-workspace/blob/feature/streaming-affect-motion/docs/checkpoints/2026-09-22-conversation-face-handoff.md)
for working English/Cantonese conversation, speech/ROS foundations and the next
mouth/expression integration and small-decision-model plans. This main checkout
retains planning entry points; do not start a second implementation here.

- Workspace scaffolded; no actuator commands have been sent.
- Detected controller: Pololu Mini Maestro 12-Channel USB Servo Controller (`1ffb:008a`, serial `00037376`).
- Expected ports: `/dev/ttyACM0` and `/dev/ttyACM1`; use stable `/dev/serial/by-id/...` paths in configuration.
- Motor-driver make, channel wiring, limits, and polarity are not yet verified.
- Reference repository and historical Alice research are documented under `docs/discovery/`.

## Safety gate

Do not energize or move hardware until the wiring map, mechanical travel limits, emergency stop, current limits, and a low-power bench procedure are documented and reviewed. Development should default to recorded data, mocks, and simulation.

## Layout

- `agents/`: specialized agent charters
- `docs/discovery/`: evidence and open questions
- `docs/architecture/`: decisions and system design
- `hardware/`: inventory, wiring, calibration, and safe bring-up
- `infra/`: host bootstrap, machine profile, udev policy, and future Compose topology
- `models/`: model cards, experiments, and evaluation definitions
- `src/`: production packages (added after architecture decisions)
- `tests/`: automated tests and hardware-free contract tests

## Next decisions

1. Follow the current handoff for synchronized expressive speech and a dedicated small admission model.
2. Photograph/identify the motor drivers and trace their connections to the Maestro.
3. Share any additional repositories and datasets before choosing interfaces or ML frameworks.
4. Define measurable success criteria for face mesh, emotion inference, and motor expression.
