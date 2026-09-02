from __future__ import annotations

import numpy as np
import pytest

from alice.perception.head_pose import (
    HeadPoseCalibration,
    euler_angles,
    rotation_vector,
)


def test_rotation_vector_accepts_rigid_4x4_transform() -> None:
    angle = np.deg2rad(30)
    transform = np.eye(4)
    transform[:3, :3] = [
        [np.cos(angle), -np.sin(angle), 0],
        [np.sin(angle), np.cos(angle), 0],
        [0, 0, 1],
    ]

    result = rotation_vector(transform)

    assert result == pytest.approx([0, 0, angle])


def test_euler_angles_extract_yaw_pitch_and_roll() -> None:
    yaw, pitch, roll = np.deg2rad([20.0, -10.0, 15.0])
    rx = np.array(
        [
            [1, 0, 0],
            [0, np.cos(pitch), -np.sin(pitch)],
            [0, np.sin(pitch), np.cos(pitch)],
        ]
    )
    ry = np.array(
        [[np.cos(yaw), 0, np.sin(yaw)], [0, 1, 0], [-np.sin(yaw), 0, np.cos(yaw)]]
    )
    rz = np.array(
        [[np.cos(roll), -np.sin(roll), 0], [np.sin(roll), np.cos(roll), 0], [0, 0, 1]]
    )
    transform = np.eye(4)
    transform[:3, :3] = rz @ ry @ rx

    assert euler_angles(transform) == pytest.approx([yaw, pitch, roll])


def test_calibration_recovers_intent_from_permuted_coupled_axes() -> None:
    basis = np.array(
        [
            [0.1, 0.8, 0.2],
            [0.9, 0.1, -0.1],
            [0.2, -0.2, 0.7],
        ]
    )
    calibration = HeadPoseCalibration(neutral=np.array([0.2, -0.1, 0.3]), basis=basis)
    intended = np.array([0.6, -0.4, 0.25])

    assert calibration.normalize(
        calibration.neutral + basis @ intended
    ) == pytest.approx(intended)


def test_calibration_clamps_outside_demonstrated_range() -> None:
    calibration = HeadPoseCalibration(neutral=np.zeros(3), basis=np.eye(3))

    assert calibration.normalize(np.array([2.0, -3.0, 0.5])) == pytest.approx(
        [1.0, -1.0, 0.5]
    )


def test_calibration_rejects_singular_pose_demonstrations() -> None:
    with pytest.raises(ValueError, match="independent"):
        HeadPoseCalibration(neutral=np.zeros(3), basis=np.ones((3, 3)))
