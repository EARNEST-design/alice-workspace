# Alice description v0.1

An original, approximate ROS 2 model for planning head/body reassembly.
It includes the 11 observed head/face actuator functions and 10 **hypothesized**
body hinges. The body count, axes and motor assignments are not verified.
The assumed total height is **0.62 m**. Every dimension and joint display limit
is provisional. The neutral visual pose is not the hardware Home pose.

## ROS 2

Test target: ROS 2 Lyrical. From this worktree on a configured ROS 2 system:

```bash
python -m colcon build --base-paths ros2_ws/src --packages-select alice_description
source install/setup.bash
ros2 launch alice_description display.launch.py gui:=false rviz:=false
```

Use `height_m:=0.70` to change assumed scale. Use `gui:=false rviz:=false` for
headless publication. The launch runs only visualization nodes; it publishes
`/alice_preview/joint_states` and `/alice_preview/robot_description`, with
`alice_preview/` TF frame prefixes. Synthetic poses may be sent to
`/alice_preview/pose_input` as `sensor_msgs/msg/JointState`.

The display has no serial driver, ros2_control plugin, transmission, device
access or PWM conversion. Channel 7 is excluded. The motor register is inventory
metadata, not an actuator configuration. Rigid face proxies illustrate the
actuators but do not model skin deformation or actual linkage mechanics.

## Editable model resources

- `config/geometry.yaml`: unmeasured link proportions in metres.
- `urdf/body.xacro`: provisional body chain and shell shapes.
- `urdf/head.xacro`: schematic head chain and expression parts.
- `config/motor_map.json`: observed semantic channel mappings and unknown body
  assignments. Left/right are always from the robot's perspective.

The original branch also contains a browser export tool; it was not imported
into this focused ROS migration. Use the Xacro source as the editable master.
An expanded URDF can be generated with `xacro urdf/alice.urdf.xacro`.

## Scope

This is suitable for naming parts, inspecting articulation and collecting wiring
facts. It does not establish cable lengths, connector pinouts, electrical power
requirements or mechanical clearance. Collision boxes are approximate envelopes.
Inertial data is intentionally absent; this is not a dynamics, walking or
hardware-control model. Measure pivot locations and link lengths before using
it for motion planning, and identify physical motors before assigning body IDs.

The supplied photo guided appearance only and is not included in this package.
No external robot meshes or reference repository code were copied. Package
metadata does not grant a redistribution license for the repository.

See `hardware/robot-description-assumptions.md` and ADR 0013 in the worktree.
ROS behavior follows the official
[robot_state_publisher interface](https://github.com/ros/robot_state_publisher)
and [ROS coordinate conventions](https://github.com/ros-infrastructure/rep/blob/master/rep-0103.rst).

## Headless Compose preview

From the repository root:

```bash
python3 infra/ros2/deploy.py build
python3 infra/ros2/deploy.py preview
```

The opt-in profile uses separate publisher containers. The Lyrical topology
suite is `tests/test_robot_description.py`; the actual synthetic joint-state
and TF observer is `tests/ros2/description_smoke.py`. See `docs/ros2.md` and the
dated runtime experiment for the exact qualification results and commands.

## Migration provenance

Selectively imported from user-supplied `codex/robot-description` commit
`13c25490d78ccec05a6f2714deafb5c39c60862b`. Package geometry remains provisional.
The Compose preview is headless; RViz and slider GUI require a separate local
ROS installation with those optional packages. No PWM conversion is provided.
