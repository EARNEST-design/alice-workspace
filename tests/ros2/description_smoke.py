#!/usr/bin/env python3
"""Observe actual ROS preview nodes and inject synthetic display poses only."""

import math
import time

import rclpy
from rclpy.qos import DurabilityPolicy, QoSProfile
from sensor_msgs.msg import JointState
from tf2_msgs.msg import TFMessage


def main():
    rclpy.init()
    node = rclpy.create_node("alice_description_smoke")
    frames, states = {}, set()
    static_qos = QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL)

    def on_tf(message):
        for frame in message.transforms:
            if frame.child_frame_id.startswith("alice_preview/"):
                frames[frame.child_frame_id] = frame.transform

    node.create_subscription(TFMessage, "/tf", on_tf, 20)
    node.create_subscription(TFMessage, "/tf_static", on_tf, static_qos)
    node.create_subscription(
        JointState,
        "/alice_preview/joint_states",
        lambda msg: states.update(msg.name),
        10,
    )
    publisher = node.create_publisher(JointState, "/alice_preview/pose_input", 10)
    deadline = time.monotonic() + 25
    try:
        while time.monotonic() < deadline:
            msg = JointState()
            msg.header.stamp = node.get_clock().now().to_msg()
            msg.name = ["neck_rotation", "left_shoulder_pitch", "upper_eyelids"]
            msg.position = [0.2, 0.35, 0.008]
            publisher.publish(msg)
            rclpy.spin_once(node, timeout_sec=0.1)
            required = [
                "neck_yaw_link",
                "left_upper_arm",
                "left_upper_lid",
                "right_upper_lid",
                "left_sole",
                "head_top",
            ]
            if not all("alice_preview/" + name in frames for name in required):
                continue
            neck = frames["alice_preview/neck_yaw_link"].rotation
            arm = frames["alice_preview/left_upper_arm"].rotation
            lid_l = frames["alice_preview/left_upper_lid"].translation.z
            lid_r = frames["alice_preview/right_upper_lid"].translation.z
            if (
                math.isclose(neck.z, math.sin(0.1), abs_tol=1e-5)
                and math.isclose(arm.y, -math.sin(0.175), abs_tol=1e-5)
                and math.isclose(lid_l, 0.086, abs_tol=1e-5)
                and math.isclose(lid_r, 0.086, abs_tol=1e-5)
            ):
                assert {
                    "neck_rotation",
                    "left_shoulder_pitch",
                    "upper_eyelids",
                } <= states
                print(
                    f"PASS: {len(states)} states, {len(frames)} prefixed TF frames; "
                    "head, body and mimic transforms update."
                )
                return
        raise AssertionError(
            f"Preview transform contract timed out; {len(states)} states, "
            f"{len(frames)} frames"
        )
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
