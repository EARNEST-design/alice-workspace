# Reference repository assessment

Reference: [`venetanji/alice-feedback`](https://github.com/venetanji/alice-feedback), inspected read-only on 2026-08-28.

## What it demonstrates

The small Python project links two camera views, MediaPipe's 52 blendshape features, a PyTorch MLP or iterative Jacobian optimizer, normalized motor commands, and a Pololu Maestro/optional ROS 2 actuation path. It includes calibration, recording, online training, mimicry controls, and checkpoints.

Concepts worth retaining independently:

- explicit perception/model/actuation separation;
- normalized actuator targets backed by per-channel min/neutral/max calibration;
- simulation and hardware adapters;
- robot-camera visual feedback rather than only open-loop imitation;
- ROS boundary plus standalone tooling;
- record, calibrate, train, and evaluate workflows.

## Clean-room rationale

The repository has no root license file (despite package metadata mentioning MIT), no tests or CI, and several hardware-safety and reproducibility gaps. Therefore this workspace will not copy its source. Ideas will be independently implemented behind typed, tested contracts after licensing and system decisions are recorded.

Important gaps to address include hard-coded device/config assumptions, unsafe lifecycle movement, silent hardware-to-simulation fallback, missing slew/current/watchdog/interlock controls, underspecified loss-of-face behavior, weak ROS message semantics, synchronous latency/jitter risks, unversioned online data, no held-out evaluation, unbounded model output, and absent model/data provenance.

## Proposed system boundary

Build a modular monorepo containing perception, affect/feature representation, a calibrated actuator HAL (mock and Maestro), a real-time safety supervisor, motion/model service, capture/replay, evaluation, ROS 2 adapter, and operator UI. Progress through replay/simulation, disconnected protocol tests, one-channel current-limited bench tests, then full-face trials.

This is an initial bearing, not an architecture decision. Additional repositories and hardware evidence may change it.

