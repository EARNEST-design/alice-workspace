"""Direct frontal-camera yaw/pitch/roll mimicry with verified Home cleanup."""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
import serial
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from alice.hardware.manifest import load_manifest
from alice.perception.head_pose import euler_angles

ROOT = Path(__file__).parents[1]
MODEL = Path(
    "/home/alice/alice-workspace/.worktrees/phase-1-passive-blendshapes/artifacts/passive-alice-face-pilot/model/face_landmarker.task"
)
HUMAN = "/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920_BF4BEEAF-video-index0"
ROBOT = "/dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index0"


def normalized_angle(angle_radians: float, limit_degrees: float) -> float:
    """Scale an absolute frontal-camera angle with a two-degree center dead zone."""

    degrees = float(np.rad2deg(angle_radians))
    if abs(degrees) <= 2:
        return 0.0
    adjusted = np.sign(degrees) * (abs(degrees) - 2)
    return float(np.clip(adjusted / (limit_degrees - 2), -1, 1))


def main() -> None:
    manifest = load_manifest(ROOT / "hardware/alice-face-v1.yaml")
    actuators = list(manifest.actuators)
    by_name = {actuator.name: actuator for actuator in actuators}
    human, robot = cv2.VideoCapture(HUMAN), cv2.VideoCapture(ROBOT)
    for camera in (human, robot):
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        camera.set(cv2.CAP_PROP_FPS, 10)
    if not human.isOpened() or not robot.isOpened():
        raise RuntimeError("failed to open both cameras")
    detector = vision.FaceLandmarker.create_from_options(
        vision.FaceLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=str(MODEL)),
            num_faces=1,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=True,
        )
    )
    port = serial.Serial(
        manifest.controller.command_device_path,
        9600,
        timeout=0.3,
        write_timeout=0.3,
    )

    def write(channel: int, target: int) -> None:
        port.write(bytes((0x84, channel, target & 127, (target >> 7) & 127)))
        port.flush()

    def position(channel: int) -> int:
        port.write(bytes((0x90, channel)))
        port.flush()
        data = port.read(2)
        if len(data) != 2:
            raise RuntimeError("short Maestro position read")
        return int.from_bytes(data, "little")

    def verified_home() -> None:
        for actuator in actuators:
            write(actuator.channel, actuator.home_qus)
        pending = {actuator.name: actuator for actuator in actuators}
        deadline = time.monotonic() + 15
        while pending and time.monotonic() < deadline:
            for name, actuator in list(pending.items()):
                if position(actuator.channel) == actuator.home_qus:
                    del pending[name]
            if pending:
                time.sleep(0.05)
        if pending:
            raise RuntimeError(f"Home timeout: {sorted(pending)}")

    command = np.zeros(3)
    target_names = ("neck_rotation", "face_pitch", "head_tilt")
    try:
        verified_home()
        cv2.namedWindow("Direct head yaw / pitch / roll", cv2.WINDOW_NORMAL)
        while True:
            ok_h, frame_h = human.read()
            ok_r, frame_r = robot.read()
            if not ok_h or not ok_r:
                raise RuntimeError("camera frame loss")
            rgb = np.ascontiguousarray(frame_h[..., ::-1])
            result = detector.detect(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            )
            if result.facial_transformation_matrixes:
                yaw, pitch, roll = euler_angles(
                    result.facial_transformation_matrixes[0]
                )
                desired = np.array(
                    [
                        normalized_angle(yaw, 35),
                        normalized_angle(pitch, 25),
                        normalized_angle(roll, 25),
                    ]
                )
                command += np.clip(0.25 * (desired - command), -0.05, 0.05)
                for name, value in zip(target_names, command):
                    actuator = by_name[name]
                    write(actuator.channel, actuator.target_qus(float(value)))
            view = np.hstack(
                (cv2.resize(frame_h, (640, 480)), cv2.resize(frame_r, (640, 480)))
            )
            cv2.putText(
                view,
                "YOU - C920",
                (15, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2,
            )
            cv2.putText(
                view,
                "ALICE - C525",
                (655, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 255),
                2,
            )
            message = (
                f"yaw={command[0]:+.2f} pitch={command[1]:+.2f} "
                f"roll={command[2]:+.2f}  q=stop"
            )
            cv2.putText(
                view,
                message,
                (365, 465),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
            )
            cv2.imshow("Direct head yaw / pitch / roll", view)
            if cv2.waitKey(1) & 255 in (ord("q"), 27):
                break
    finally:
        verified_home()
        print("all_channels_home_verified", flush=True)
        for actuator in actuators:
            write(actuator.channel, 0)
        time.sleep(0.1)
        if any(position(actuator.channel) != 0 for actuator in actuators):
            raise RuntimeError("controller output disable verification failed")
        print("all_outputs_disabled", flush=True)
        port.close()
        human.release()
        robot.release()
        detector.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
