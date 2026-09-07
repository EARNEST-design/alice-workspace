"""Calibrate human horizontal gaze and map it to Alice's coupled eye range."""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import serial

from alice.control.virtual_actuators import HorizontalGaze, expand_horizontal_gaze
from alice.hardware.manifest import load_manifest
from alice.perception.mediapipe_adapter import MediaPipeTaskDetector

ROOT = Path(__file__).parents[1]
MODEL = Path(
    "/home/alice/alice-workspace/.worktrees/phase-1-passive-blendshapes/artifacts/passive-alice-face-pilot/model/face_landmarker.task"
)
HUMAN = "/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920_BF4BEEAF-video-index0"
ROBOT = "/dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index0"


def gaze_score(scores: dict[str, float]) -> float | None:
    required = {
        "eyeLookOutLeft",
        "eyeLookInRight",
        "eyeLookInLeft",
        "eyeLookOutRight",
    }
    if not required <= scores.keys():
        return None
    left = (scores["eyeLookOutLeft"] + scores["eyeLookInRight"]) / 2
    right = (scores["eyeLookInLeft"] + scores["eyeLookOutRight"]) / 2
    return left - right


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
    detector = MediaPipeTaskDetector.from_model_path(MODEL)
    port = serial.Serial(
        manifest.controller.command_device_path,
        9600,
        timeout=0.3,
        write_timeout=0.3,
    )

    def write(channel: int, target: int) -> None:
        port.write(bytes((0x84, channel, target & 127, (target >> 7) & 127)))

    right_samples: list[float] = []
    left_samples: list[float] = []
    smoothed = 0.0
    targets = (6000, 6000)
    try:
        for actuator in actuators:
            write(actuator.channel, actuator.home_qus)
        port.flush()
        time.sleep(1)
        cv2.namedWindow("Coupled gaze range mimicry", cv2.WINDOW_NORMAL)
        while True:
            ok_h, frame_h = human.read()
            ok_r, frame_r = robot.read()
            if not ok_h or not ok_r:
                raise RuntimeError("camera frame loss")
            scores = dict(
                detector.detect_preview(cv2.cvtColor(frame_h, cv2.COLOR_BGR2RGB)).scores
            )
            raw = gaze_score(scores)
            normalized = smoothed
            if raw is not None:
                if len(right_samples) < 30:
                    right_samples.append(raw)
                elif len(left_samples) < 30:
                    left_samples.append(raw)
                else:
                    right = float(np.median(right_samples))
                    left = float(np.median(left_samples))
                    if left <= right + 0.02:
                        raise RuntimeError(
                            "human gaze calibration range is too small or reversed: "
                            f"right={right:.4f} left={left:.4f}"
                        )
                    normalized = float(
                        np.clip(2 * (raw - right) / (left - right) - 1, -1, 1)
                    )
                    smoothed += 0.3 * (normalized - smoothed)
                    expanded = expand_horizontal_gaze(HorizontalGaze(position=smoothed))
                    physical = []
                    for item in expanded:
                        actuator = by_name[item.actuator_name]
                        pulse = actuator.target_qus(item.normalized_position)
                        write(actuator.channel, pulse)
                        physical.append(pulse)
                    targets = tuple(physical)
                    port.flush()
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
            if len(right_samples) < 30:
                message = f"LOOK FULLY RIGHT {len(right_samples)}/30"
            elif len(left_samples) < 30:
                message = f"LOOK FULLY LEFT {len(left_samples)}/30"
            else:
                message = (
                    f"LIVE gaze={smoothed:+.2f} right={targets[0]} "
                    f"left={targets[1]}  q=stop"
                )
            cv2.putText(
                view,
                message,
                (300, 465),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
            )
            cv2.imshow("Coupled gaze range mimicry", view)
            if cv2.waitKey(1) & 255 in (ord("q"), 27):
                break
    finally:
        for actuator in actuators:
            write(actuator.channel, actuator.home_qus)
        port.flush()
        time.sleep(0.5)
        for actuator in actuators:
            write(actuator.channel, 0)
        port.flush()
        print("all_outputs_disabled", flush=True)
        port.close()
        human.release()
        robot.release()
        cv2.destroyAllWindows()
        try:
            detector.close()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
