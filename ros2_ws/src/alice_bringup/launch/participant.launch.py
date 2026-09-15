"""Launch one idle participant. Compose owns process/container separation."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "role",
                choices=[
                    "session",
                    "tts",
                    "audio",
                    "expression",
                    "motion",
                    "maestro",
                    "perception",
                    "recorder",
                ],
            ),
            Node(
                package="alice_nodes",
                executable=LaunchConfiguration("role"),
                output="screen",
                parameters=[
                    {
                        "config_root": "/opt/alice/config",
                        "hardware_root": "/opt/alice/hardware",
                        "fixtures_root": "/fixtures",
                        "output_root": "/artifacts",
                    }
                ],
            ),
        ]
    )
