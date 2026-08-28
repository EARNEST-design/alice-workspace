# ADR 0001: Container-first runtime with a thin managed host

- Status: accepted
- Date: 2026-08-28

## Context

Alice needs reproducible perception, ML, ROS 2, control, and operator services while retaining reliable access to cameras and the Pololu Maestro. The dedicated host should be replaceable without turning its current package state into undocumented infrastructure.

## Decision

Run application and ML services in Docker Compose by default. Keep the host responsible for OS/kernel drivers, Docker, udev device permissions, networking, administration, and hardware diagnostics that require native USB. Hardware containers receive only explicitly named device nodes; ordinary services do not run privileged and do not mount the Docker socket.

Use stable `/dev/serial/by-id/` paths and versioned configuration. Separate hardware-enabled Compose profiles from simulation/replay defaults. Pin production images and record the host/bootstrap state in `infra/host/`.

## Consequences

ROS 2 networking, camera devices, real-time scheduling, audio/video acceleration, and USB permissions require explicit container configuration and host tests. Simulation remains the default, while hardware access becomes visible and reviewable. Native GUI utilities may remain host tools when container display forwarding adds no value.

