# Alice Robot Description Implementation Plan

**Goal:** A reviewable ROS 2 visualization model for body/head reassembly.
**Architecture:** Xacro owns geometry; a JSON wiring register owns evidence and
motor associations; a browser preview consumes the expanded URDF.
**Tech stack:** Python, Xacro, URDF, ament_cmake, ROS 2 Jazzy, Three.js.
**Spec:** `docs/architecture/0006-robot-description-for-reassembly.md`.

## Constraints

Preserve the speech worktree. All dimensions and body articulation are
provisional. No hardware communication or measured dynamics claims. Exclude
channel 7. Keep `/alice_preview` joint topics and `alice_preview/` frame prefix.

## Task 1: Geometry and motor contract

- [x] Create `tests/test_robot_description.py`: expand the actual Xacro and
  parse with urdf_parser_py; check a connected tree, motor assignments, paired
  lid mimic behavior, geometry scaling, standing soles and head height.
- [x] Run `.venv/bin/python -m pytest tests/test_robot_description.py -q`;
  confirm missing-model failure before creating model files.
- [x] Create `src/alice_description/urdf/alice.urdf.xacro`,
  `urdf/body.xacro`, `urdf/head.xacro`, `urdf/primitives.xacro`,
  `config/geometry.yaml`, and `config/motor_map.json`.
- [x] Run the same contract tests and fix defects.

## Task 2: ROS package and real runtime check

- [x] Create `package.xml`, `CMakeLists.txt`, `launch/display.launch.py`,
  `rviz/alice.rviz` and `tools/ros_description_smoke.py`.
- [x] Build with `colcon build --packages-select alice_description` in a
  disposable Jazzy container; launch with `gui:=false rviz:=false`.
- [x] Subscribe to the actual namespaced JointState stream and prefixed TF;
  assert head, body and mimic frames are present and inputs update transforms.

## Task 3: Preview and handoff

- [x] Create `tools/export_robot_preview.py` and a viewer template; generate
  `artifacts/robot-description/alice.urdf`, `model.json` and `index.html`.
- [x] Visually inspect the render and change a head and a body joint.
- [x] Add hardware unknowns, launch instructions and verification evidence.
- [x] Request a QA review while finishing preview verification; fix findings.
- [x] Capture final tests and leave all work in the side worktree for review.
