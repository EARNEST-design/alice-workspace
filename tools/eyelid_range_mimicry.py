"""Calibrate human blink range and map it to Alice's coupled eyelids."""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import serial

from alice.control.virtual_actuators import EyelidAperture, expand_eyelid_aperture
from alice.hardware.manifest import load_manifest
from alice.perception.mediapipe_adapter import MediaPipeTaskDetector

ROOT = Path(__file__).parents[1]
MODEL = Path(
    "/home/alice/alice-workspace/.worktrees/phase-1-passive-blendshapes/artifacts/passive-alice-face-pilot/model/face_landmarker.task"
)
HUMAN = "/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920_BF4BEEAF-video-index0"
ROBOT = "/dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index0"


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
    detector = MediaPipeTaskDetector.from_model_path(MODEL)
    port = serial.Serial(
        manifest.controller.command_device_path,
        9600,
        timeout=0.3,
        write_timeout=0.3,
    )

    def write(channel: int, target: int) -> None:
        port.write(bytes((0x84, channel, target & 127, (target >> 7) & 127)))

    open_samples: list[float] = []
    closed_samples: list[float] = []
    smoothed = 0.0
    targets = (by_name["lower_eyelids"].home_qus, by_name["upper_eyelids"].home_qus)
    try:
        for actuator in actuators:
            write(actuator.channel, actuator.home_qus)
        port.flush()
        time.sleep(1)
        cv2.namedWindow("Coupled eyelid aperture mimicry", cv2.WINDOW_NORMAL)
        while True:
            ok_h, frame_h = human.read()
            ok_r, frame_r = robot.read()
            if not ok_h or not ok_r:
                raise RuntimeError("camera frame loss")
            scores = dict(
                detector.detect_preview(cv2.cvtColor(frame_h, cv2.COLOR_BGR2RGB)).scores
            )
            raw = blink_score(scores)
            aperture = smoothed
            if raw is not None:
                if len(open_samples) < 30:
                    open_samples.append(raw)
                elif len(closed_samples) < 30:
                    closed_samples.append(raw)
                else:
                    opened = float(np.median(open_samples))
                    closed = float(np.median(closed_samples))
                    if closed <= opened + 0.005:
                        raise RuntimeError(
                            "human eyelid calibration range is too small or reversed: "
                            f"open={opened:.4f} closed={closed:.4f}"
                        )
                    closure = float(np.clip((raw - opened) / (closed - opened), 0, 1))
                    # A normal open eye is the Home expression, not the
                    # surprised/full-open endpoint. Positive aperture is
                    # reserved for a separately calibrated wide-eye gesture.
                    aperture = -closure
                    smoothed += 0.35 * (aperture - smoothed)
                    expanded = expand_eyelid_aperture(EyelidAperture(position=smoothed))
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
            if len(open_samples) < 30:
                message = f"KEEP EYES FULLY OPEN {len(open_samples)}/30"
            elif len(closed_samples) < 30:
                message = f"KEEP EYES CLOSED {len(closed_samples)}/30"
            else:
                message = (
                    f"LIVE aperture={smoothed:+.2f} lower={targets[0]} "
                    f"upper={targets[1]}  q=stop"
                )
            cv2.putText(
                view,
                message,
                (285, 465),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
            )
            cv2.imshow("Coupled eyelid aperture mimicry", view)
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
