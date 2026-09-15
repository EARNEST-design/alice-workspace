"""Display-only launch. There is no hardware adapter in this package."""
import math
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
import xacro


def preview_nodes(context):
    height = float(LaunchConfiguration("height_m").perform(context))
    if not math.isfinite(height) or not 0.1 <= height <= 3.0:
        raise ValueError("height_m must be a finite value between 0.1 and 3.0 metres")
    share = Path(get_package_share_directory("alice_description"))
    description = xacro.process_file(
        str(share / "urdf/alice.urdf.xacro"), mappings={"height_m": str(height)}
    ).toxml()
    parameters = {"robot_description": description}
    common = {"namespace": "alice_preview", "output": "screen"}
    return [
        Node(package="robot_state_publisher", executable="robot_state_publisher",
             parameters=[parameters, {"frame_prefix": "alice_preview/"}], **common),
        Node(package="joint_state_publisher_gui", executable="joint_state_publisher_gui",
             parameters=[parameters, {"source_list": ["pose_input"]}],
             condition=IfCondition(LaunchConfiguration("gui")), **common),
        Node(package="joint_state_publisher", executable="joint_state_publisher",
             parameters=[parameters, {"source_list": ["pose_input"]}],
             condition=UnlessCondition(LaunchConfiguration("gui")), **common),
        Node(package="rviz2", executable="rviz2", arguments=["-d", str(share / "rviz/alice.rviz")],
             condition=IfCondition(LaunchConfiguration("rviz")), **common),
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("height_m", default_value="0.62", description="Assumed visualization height in metres"),
        DeclareLaunchArgument("gui", default_value="true", description="Show joint sliders"),
        DeclareLaunchArgument("rviz", default_value="true", description="Show RViz"),
        OpaqueFunction(function=preview_nodes),
    ])
