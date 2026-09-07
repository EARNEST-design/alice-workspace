"""Integrated mouth, gaze, eyelid, and conservative head-pose mimicry."""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
import serial
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from alice.control.virtual_actuators import (
    EyelidAperture,
    HorizontalGaze,
    expand_eyelid_aperture,
    expand_horizontal_gaze,
)
from alice.hardware.manifest import load_manifest
from alice.perception.head_pose import euler_angles

ROOT = Path(__file__).parents[1]
MODEL = Path(
    "/home/alice/alice-workspace/.worktrees/phase-1-passive-blendshapes/artifacts/passive-alice-face-pilot/model/face_landmarker.task"
)
HUMAN = "/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920_BF4BEEAF-video-index0"
ROBOT = "/dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index0"


def scale_angle(angle_radians: float, limit_degrees: float, cap: float) -> float:
    degrees = float(np.rad2deg(angle_radians))
    if abs(degrees) <= 2:
        return 0.0
    adjusted = np.sign(degrees) * (abs(degrees) - 2) / (limit_degrees - 2)
    return float(np.clip(adjusted, -cap, cap))


def gaze_score(scores: dict[str, float]) -> float | None:
    names = (
        "eyeLookOutLeft",
        "eyeLookInRight",
        "eyeLookInLeft",
        "eyeLookOutRight",
    )
    if not all(name in scores for name in names):
        return None
    left = (scores["eyeLookOutLeft"] + scores["eyeLookInRight"]) / 2
    right = (scores["eyeLookInLeft"] + scores["eyeLookOutRight"]) / 2
    return left - right


def blink_score(scores: dict[str, float]) -> float | None:
    if "eyeBlinkLeft" not in scores or "eyeBlinkRight" not in scores:
        return None
    return (scores["eyeBlinkLeft"] + scores["eyeBlinkRight"]) / 2


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
            output_face_blendshapes=True,
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

    baseline_samples: list[tuple[float, float, float]] = []
    baseline: tuple[float, float, float] | None = None
    mouth_span = 0.35
    gaze_span = 0.15
    blink_span = 0.25
    mouth_value = 0.5
    gaze_value = 0.0
    aperture_value = 0.0
    head_value = np.zeros(3)
    try:
        verified_home()
        cv2.namedWindow("Alice integrated mimicry", cv2.WINDOW_NORMAL)
        while True:
            ok_h, frame_h = human.read()
            ok_r, frame_r = robot.read()
            if not ok_h or not ok_r:
                raise RuntimeError("camera frame loss")
            rgb = np.ascontiguousarray(frame_h[..., ::-1])
            result = detector.detect(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            )
            scores: dict[str, float] = {}
            if result.face_blendshapes:
                scores = {
                    item.category_name: float(item.score)
                    for item in result.face_blendshapes[0]
                }
            jaw = scores.get("jawOpen")
            gaze = gaze_score(scores)
            blink = blink_score(scores)
            if jaw is not None and gaze is not None and blink is not None:
                if baseline is None:
                    baseline_samples.append((jaw, gaze, blink))
                    if len(baseline_samples) >= 20:
                        values = np.median(np.asarray(baseline_samples), axis=0)
                        baseline = (
                            float(values[0]),
                            float(values[1]),
                            float(values[2]),
                        )
                    continue
                neutral_jaw, neutral_gaze, neutral_blink = baseline
                mouth_span = max(mouth_span, jaw - neutral_jaw)
                gaze_span = max(gaze_span, abs(gaze - neutral_gaze))
                blink_span = max(blink_span, blink - neutral_blink)
                desired_mouth = float(np.clip((jaw - neutral_jaw) / mouth_span, 0, 1))
                desired_gaze = float(np.clip((gaze - neutral_gaze) / gaze_span, -1, 1))
                closure = float(np.clip((blink - neutral_blink) / blink_span, 0, 1))
                mouth_value += 0.25 * (desired_mouth - mouth_value)
                gaze_value += 0.3 * (desired_gaze - gaze_value)
                aperture_value += 0.35 * (-closure - aperture_value)

                mouth = by_name["mouth_open"]
                mouth_target = round(
                    mouth.software_min_qus
                    + mouth_value * (mouth.software_max_qus - mouth.software_min_qus)
                )
                write(mouth.channel, mouth_target)
                for virtual in (
                    expand_horizontal_gaze(HorizontalGaze(position=gaze_value)),
                    expand_eyelid_aperture(EyelidAperture(position=aperture_value)),
                ):
                    for item in virtual:
                        actuator = by_name[item.actuator_name]
                        write(
                            actuator.channel,
                            actuator.target_qus(item.normalized_position),
                        )

                if result.facial_transformation_matrixes:
                    yaw, pitch, roll = euler_angles(
                        result.facial_transformation_matrixes[0]
                    )
                    desired_head = np.array(
                        [
                            scale_angle(yaw, 35, 0.65),
                            scale_angle(pitch, 25, 0.4),
                            scale_angle(roll, 25, 0.55),
                        ]
                    )
                    head_value += np.clip(
                        0.2 * (desired_head - head_value), -0.04, 0.04
                    )
                    for name, value in zip(
                        ("neck_rotation", "face_pitch", "head_tilt"), head_value
                    ):
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
            if baseline is None:
                message = f"STARTING {len(baseline_samples)}/20"
            else:
                message = (
                    f"mouth={mouth_value:.2f} gaze={gaze_value:+.2f} "
                    f"eyes={aperture_value:+.2f} head="
                    f"{head_value[0]:+.2f}/{head_value[1]:+.2f}/{head_value[2]:+.2f}"
                )
            cv2.putText(
                view,
                message,
                (260, 465),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (255, 255, 255),
                2,
            )
            cv2.imshow("Alice integrated mimicry", view)
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
