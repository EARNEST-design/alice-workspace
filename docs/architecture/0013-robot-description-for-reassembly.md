# ADR 0013: Robot description for body/head reassembly

Date: 2026-09-15. Status: provisional visualization design.

## Context

The user requested a ROS 2 compatible 3D model for planning body/head rewiring,
using an attached photograph and recorded motor functions. The photograph is a
visual reference, not a source of operating instructions. It shows shell shapes
but cannot establish scale, hidden mechanisms, motor models, wiring or pinouts.
The local motor map establishes 11 connected head/face channels; body joints are
uncharacterized. Existing speech work and local experiments must be preserved.

## Decision

Create an independent `alice_description` ament package on
`codex/robot-description`. Use original parametric primitive geometry in Xacro,
with an expanded URDF and an interactive viewer of that same URDF. Use ROS SI
units and x-forward/y-left/z-up coordinates. Treat left/right as the robot's.

All physical dimensions are estimates. Start with a **0.62 m assumed height**,
not a measurement or a verified product specification. Model one provisional
pitch hinge at each shoulder, elbow, hip, knee and ankle (10 body joints). This
is a minimum visual articulation hypothesis, not a body motor inventory. Keep
the waist fixed until its actual mechanism is established.

Represent head channels 0/1/2 as yaw/roll/pitch. Their serial order, pivot
locations, directions and travel are assumptions. Represent 3/4 as paired lid
proxies using mimic joints, 5 as a forehead proxy, 6 as a jaw hinge, 8/10 as
independent eye yaw, and 9/11 as independent vertical mouth-corner proxies.
URDF rigid geometry cannot reproduce deformable facial skin or coupled linkages.

Store semantic motor associations and evidence in a separate wiring register.
Never convert Maestro pulse widths to angles without measured linkage geometry.
Body controller IDs, channels, connectors, power and protocol remain null.
Channel 7 is absent. URDF limits are display ranges, not calibrated limits.

Launch only robot_state_publisher, joint_state_publisher[_gui], and RViz. Use
the `/alice_preview` namespace for joint states and robot description, plus
prefixed TF frames, to separate preview data. No hardware driver, transmission,
ros2_control plugin, PWM bridge or serial access is part of this package.
Omit inertial data instead of inventing dynamics. Collision shapes are coarse
visual planning envelopes, not certified clearance bounds.

## Acceptance

- Xacro expands and the standard URDF parser accepts one connected tree.
- Motor register agrees with all 11 observed channels and excludes channel 7.
- Body assignments are explicitly unverified and unassigned.
- Paired lids share their driver's state through mimic joints.
- Scaling doubles all lengths without altering angular limits or motor IDs.
- ROS 2 container build and headless publication exercise actual launch nodes.
- The viewer loads the same expanded geometry and visibly changes joint poses.

## Next evidence

Record total height, joint-center distances and head mounting offsets; front,
side and rear body photographs; motor/board labels; connector labels and traced
endpoints; measured joint travel/direction; and head gimbal assembly order.
Exact wiring/power design and hardware execution are subsequent work.

## References

- `hardware/motor-map.md` and `hardware/reference-motor-calibration.md`.
- User attachment `codex-clipboard-e9b86220-2e09-4992-9b97-b63870878479.png`;
  photograph credited within the image to Fred van Diem. Not redistributed.
- [ROS coordinate conventions](https://github.com/ros-infrastructure/rep/blob/master/rep-0103.rst).
- [robot_state_publisher](https://github.com/ros/robot_state_publisher).

No reference repository code or external robot meshes were reused.

## ROS migration provenance and scope

This decision originated as ADR0006 on user-supplied `codex/robot-description`
commit13c25490d78ccec05a6f2714deafb5c39c60862b. It is renumbered because this
worktree already assigns0006 to streaming. Only the model package, topology
tests, TF smoke and assumptions are imported under `ros2_ws/src/alice_description`.
The browser export and its acceptance remain on the source branch; they are
not claimed as migrated. Lyrical Compose adds two optional headless publisher
services under `/alice_preview`, with no semantic/PWM conversion or new hardware
scope. The dated experiment records new qualification separately.
