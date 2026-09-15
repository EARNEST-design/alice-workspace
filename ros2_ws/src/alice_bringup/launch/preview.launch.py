"""One headless preview process per container; synthetic SI joints only."""

from pathlib import Path

import xacro
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def participant(context):
    role = LaunchConfiguration("role").perform(context)
    share = Path(get_package_share_directory("alice_description"))
    description = xacro.process_file(str(share / "urdf/alice.urdf.xacro")).toxml()
    return [
        Node(
            package=role,
            executable=role,
            namespace="alice_preview",
            output="screen",
            parameters=[
                {
                    "robot_description": description,
                    "frame_prefix": "alice_preview/",
                    "source_list": ["pose_input"],
                }
            ],
        )
    ]


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "role", choices=["robot_state_publisher", "joint_state_publisher"]
            ),
            OpaqueFunction(function=participant),
        ]
    )
