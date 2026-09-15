# Robot description validation — 2026-09-15

## Configuration and provenance

- Branch/worktree: `codex/robot-description`, `.worktrees/robot-description`.
- Description: `alice-planning-v1`, package `alice_description` v0.1.0.
- Geometry config: `src/alice_description/config/geometry.yaml`.
- Assumed height: 0.62 m. Scaling tested at 1.24 m.
- Model register: `src/alice_description/config/motor_map.json`.
- Source: user photo, original primitive geometry and operator-confirmed local
  motor notes. No robot mesh or reference repository code reused.
- Photo SHA-256: `2f5d1af5ec8d411b960e0f1d145471849a85ffcc0ba3ec1cecaf3592485d527d`.
  The photo itself is not included in exports.
- Deterministic model generation; no training dataset, participant data,
  evaluation split or random seed applies.
- ROS tool image: `alice-description:jazzy`, based on the digest pinned in
  `infra/Dockerfile.description`. Installation package names were checked
  against the official ROS apt repository.

## Results

| Check | Result |
|---|---|
| Host contract/export tests | 15 passed |
| Jazzy container contract/export tests | 15 passed |
| `colcon build` | 1 package built and installed |
| Standard `check_urdf` | Successfully parsed one tree rooted at base_link |
| Export size | 31 links, 30 joints |
| Actuation associations | 11 observed head/face functions, 10 hypothesized body hinges |
| Independent display coordinates | 21; plus two mimic eyelid joints |
| Live JointState stream | 23 joint names |
| Live TF stream | 30 prefixed transforms |
| Synthetic pose checks | Neck yaw, left shoulder pitch, both upper-lid transforms updated correctly |
| Invalid launch height | NaN rejected before nodes start |
| Browser inspection | Visible humanoid; tucked-arm pose and 0.26 rad neck rotation worked |
| Final test-container exit | 0; no description test containers left running |
| Hardware execution | None; no device mounts or network in runtime container |

The RViz GUI was not launched. Its RobotModel topic and TF prefix configuration
were reviewed; a regression test covers frame-name resolution. Browser
inspection checks appearance and articulation, not physical geometry accuracy.

## Issues found and resolved

- The first test compared Python JointLimit object identity rather than its
  numeric fields. The test was corrected; geometry scaling remained unchanged.
- Docker bridge DNS failed. Image build used the host network; runtime remained
  isolated with no network or devices. No host DNS settings were changed.
- The ROS Python URDF parser package is `ros-jazzy-urdfdom-py`.
- QA review found the missing RViz RobotModel TF prefix. Fixed with a regression
  test before completion.
- QA review found stale height wording in scaled exports. Separate reference
  and active-height fields now have a regression test.
- The initial shell background launch ignored SIGINT at teardown. Cleanup now
  sends SIGTERM to the captured launch PID; the full rerun exited successfully.

## Artifacts

`artifacts/robot-description/` contains the expanded `alice.urdf`, embedded-data
`index.html` viewer, `model.json`, `ros-validation.log`, `metrics.json`,
`manifest.json`, and `alice-description-v0.1.zip`.

Reproduction commands are in `src/alice_description/README.md`.

## Conclusion

The package is a tested ROS 2 kinematic visualization suitable for naming parts
and recording reassembly facts. It remains an approximate model: body motor
inventory, all dimensions, head pivot order and mechanical travel need physical
evidence. It is not a calibrated dynamics or hardware-control model.
