# Alice description v0.1

An original, approximate ROS 2 model for planning head/body reassembly.
It includes the 11 observed head/face actuator functions and 10 **hypothesized**
body hinges. The body count, axes and motor assignments are not verified.
The assumed total height is **0.62 m**. Every dimension and joint display limit
is provisional. The neutral visual pose is not the hardware Home pose.

## ROS 2

Test target: ROS 2 Jazzy. From this worktree on a configured ROS 2 system:

```bash
rosdep install --from-paths src/alice_description --ignore-src -r -y
colcon build --packages-select alice_description
source install/setup.bash
ros2 launch alice_description display.launch.py
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

## Edit and export

- `config/geometry.yaml`: unmeasured link proportions in metres.
- `urdf/body.xacro`: provisional body chain and shell shapes.
- `urdf/head.xacro`: schematic head chain and expression parts.
- `config/motor_map.json`: observed semantic channel mappings and unknown body
  assignments. Left/right are always from the robot's perspective.

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r tools/requirements-description.txt
.venv/bin/python -m pytest tests/test_robot_description.py -q
.venv/bin/python tools/export_robot_preview.py
python3 -m http.server 8765 --bind 127.0.0.1 --directory artifacts/robot-description
```

Open `http://127.0.0.1:8765` for the generated 3D viewer. It embeds the expanded
URDF geometry and joint tree; Three.js loads from a pinned public CDN.
The ROS model itself has no web dependency. `alice.urdf` is a portable,
expanded description; the Xacro source is the editable master.

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

See `hardware/robot-description-assumptions.md` and ADR 0006 in the worktree.
ROS behavior follows the official
[robot_state_publisher interface](https://github.com/ros/robot_state_publisher)
and [ROS coordinate conventions](https://github.com/ros-infrastructure/rep/blob/master/rep-0103.rst).

## Reproduce the container checks

From this worktree, build the tool image, then run the package without network
or attached devices. The source tree is mounted read-only.

```bash
docker build -f infra/Dockerfile.description -t alice-description:jazzy .
docker run --rm --network none --user "$(id -u):$(id -g)" \
  --env ROS_DOMAIN_ID=91 --env ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
  --env ROS_LOG_DIR=/tmp/alice-ros-logs --env PYTHONDONTWRITEBYTECODE=1 \
  --mount "type=bind,source=$(pwd),target=/repo,readonly" \
  alice-description:jazzy bash /repo/tools/check_description_ros.sh
```

On this host the Docker bridge could not resolve DNS; the image was built with
`docker build --network host ...`. The actual ROS tests used `--network none`.
The checks build/install the package, validate the URDF with `check_urdf`, run
15 Python tests, and verify live synthetic poses reach the expected TF frames.
RViz's launch configuration is included and checked for matching frame names;
the interactive appearance check used the browser viewer, not a running RViz GUI.
