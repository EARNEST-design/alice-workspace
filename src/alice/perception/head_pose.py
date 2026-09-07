"""Head-pose rotation and camera-axis calibration utilities."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray


def rotation_vector(transform: NDArray[np.floating]) -> NDArray[np.float64]:
    """Return the Rodrigues rotation vector from a rigid 3x3 or 4x4 transform."""

    matrix = np.asarray(transform, dtype=np.float64)
    if matrix.shape == (4, 4):
        matrix = matrix[:3, :3]
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("head transform must contain a finite 3x3 rotation")
    if not np.allclose(matrix.T @ matrix, np.eye(3), atol=0.08):
        raise ValueError("head transform rotation must be approximately orthonormal")
    if not np.isclose(np.linalg.det(matrix), 1.0, atol=0.08):
        raise ValueError("head transform rotation must have determinant one")
    vector, _ = cv2.Rodrigues(matrix)
    return np.asarray(vector.reshape(3), dtype=np.float64)


def euler_angles(transform: NDArray[np.floating]) -> NDArray[np.float64]:
    """Return ZYX yaw, pitch, and roll angles in radians."""

    matrix = np.asarray(transform, dtype=np.float64)
    if matrix.shape == (4, 4):
        matrix = matrix[:3, :3]
    rotation_vector(matrix)
    horizontal = np.hypot(matrix[0, 0], matrix[1, 0])
    pitch_x = np.arctan2(matrix[2, 1], matrix[2, 2])
    yaw_y = np.arctan2(-matrix[2, 0], horizontal)
    roll_z = np.arctan2(matrix[1, 0], matrix[0, 0])
    return np.asarray([yaw_y, pitch_x, roll_z], dtype=np.float64)


@dataclass(frozen=True)
class HeadPoseCalibration:
    """Map camera rotation vectors to normalized yaw, pitch, and roll intent."""

    neutral: NDArray[np.float64]
    basis: NDArray[np.float64]

    def __post_init__(self) -> None:
        neutral = np.asarray(self.neutral, dtype=np.float64)
        basis = np.asarray(self.basis, dtype=np.float64)
        if neutral.shape != (3,) or basis.shape != (3, 3):
            raise ValueError("head calibration requires a 3-vector and 3x3 basis")
        if not np.isfinite(neutral).all() or not np.isfinite(basis).all():
            raise ValueError("head calibration values must be finite")
        if np.linalg.cond(basis) > 50:
            raise ValueError(
                "head pose demonstrations must span three independent axes"
            )
        object.__setattr__(self, "neutral", neutral)
        object.__setattr__(self, "basis", basis)

    def normalize(self, rotation: NDArray[np.floating]) -> NDArray[np.float64]:
        vector = np.asarray(rotation, dtype=np.float64)
        if vector.shape != (3,) or not np.isfinite(vector).all():
            raise ValueError("head rotation must be a finite 3-vector")
        return np.clip(np.linalg.solve(self.basis, vector - self.neutral), -1, 1)
