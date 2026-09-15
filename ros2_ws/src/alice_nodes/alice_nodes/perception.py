"""Passive replay/C525 observations. No perception-to-servo feedback path."""

import hashlib
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from alice_interfaces.msg import FaceObservation

from alice.contracts.blendshapes import BlendshapeObservation, ObservationValidity
from alice_nodes import contracts as wire
from alice_nodes.base import LATEST, RuntimeNode, spin, write_json


class PerceptionNode(RuntimeNode):
    def __init__(self, **kwargs):
        super().__init__("perception", **kwargs)
        self.declare_parameter("perception_mode", "replay")
        self.declare_parameter("replay_file", "")
        self.declare_parameter("model_path", "/models/face_landmarker.task")
        self.camera = self.detector = self.thread = None
        self.publisher = self.create_publisher(
            FaceObservation, "/alice/perception/face_observation", LATEST
        )

    def prepare_run(self):
        self.mode = self.get_parameter("perception_mode").value
        self.replay = []
        if self.mode == "replay":
            name = self.get_parameter("replay_file").value
            if name:
                if Path(name).name != name:
                    raise ValueError("replay requires one fixture basename")
                path = self.paths.fixtures / name
                if path.stat().st_size > 1_000_000:
                    raise ValueError("replay exceeds bound")
                self.replay = [
                    BlendshapeObservation.model_validate_json(line)
                    for line in path.read_text().splitlines()
                ]
                if len(self.replay) > 500:
                    raise ValueError("replay exceeds observation count")
            self.model_identity = {
                "mode": "replay",
                "provenance": "explicit replay fixture"
                if name
                else "synthetic no-face fixture",
            }
        elif self.mode == "c525":
            if (
                not self.binding.hardware
                or not self.get_parameter("hardware_enabled").value
            ):
                raise ValueError("C525 requires admitted hardware deployment/run")
            from alice.perception.camera import OpenCVCamera
            from alice.perception.mediapipe_adapter import MediaPipeBlendshapeAdapter
            from alice.speech.device_identity import (
                inspect_camera_identity,
                load_readiness_device_config,
            )

            config = load_readiness_device_config(
                self.config_root / "experiments/streaming-motion-readiness-v1.yaml"
            )
            identity = inspect_camera_identity(config.camera.stable_path, config.camera)
            self.camera = OpenCVCamera(
                camera_id="alice-c525",
                device=config.camera.stable_path,
                width=640,
                height=480,
                fps=30,
            )
            self.detector = MediaPipeBlendshapeAdapter(
                camera_id="alice-c525",
                model_path=Path(self.get_parameter("model_path").value),
            )
            self.model_identity = {
                "mode": "c525",
                "camera": identity.model_dump(mode="json"),
                "model_sha256": hashlib.sha256(
                    Path(self.get_parameter("model_path").value).read_bytes()
                ).hexdigest(),
            }
        else:
            raise ValueError("unknown perception mode")
        self.last_observation = None
        write_json(self.local_dir / "model.json", self.model_identity)

    def start_run(self):
        self.thread = threading.Thread(
            target=self._observe, name="perception-worker", daemon=True
        )
        self.thread.start()

    def _observe(self):
        try:
            if self.camera:
                self.camera.open()
            index = 0
            while not self.cancel.is_set() and self._ended is None:
                now = time.monotonic_ns()
                if self.camera:
                    frame = self.camera.read()
                    observation = self.detector.observe(frame, self.identity.run_id)
                    if self.get_parameter("retain_raw").value:
                        import cv2

                        if index < 750:
                            cv2.imwrite(
                                str(self.local_dir / f"frame-{index:04}.jpg"), frame.bgr
                            )
                elif self.replay:
                    original = self.replay[index % len(self.replay)]
                    # Replay uses a new clock; the fixture retains provenance.
                    observation = original.model_copy(
                        update={
                            "run_id": self.identity.run_id,
                            "monotonic_ns": now,
                            "captured_at": datetime.now(timezone.utc),
                        }
                    )
                else:
                    observation = BlendshapeObservation(
                        schema_version="blendshape-observation/v1",
                        captured_at=datetime.now(timezone.utc),
                        monotonic_ns=now,
                        camera_id="synthetic-replay",
                        run_id=self.identity.run_id,
                        detector="synthetic-no-face/v1",
                        detector_model_sha256=hashlib.sha256(
                            b"synthetic-no-face/v1"
                        ).hexdigest(),
                        image_width=640,
                        image_height=480,
                        face_confidence=None,
                        validity=ObservationValidity.NO_FACE,
                        invalid_reason="synthetic no face",
                        scores=(),
                    )
                self.publisher.publish(
                    wire.face_observation_to_msg(
                        observation,
                        self.header("observation", observation.monotonic_ns),
                    )
                )
                self.last_observation = time.monotonic_ns()
                self.progress += 1
                index += 1
                self.cancel.wait(0.05)
        except Exception as exc:
            self.fail(str(exc))
        finally:
            if self.camera:
                self.camera.close()
            if self.detector:
                self.detector.close()

    def check_progress(self, now):
        if (
            self._ended is None
            and self.last_observation
            and now - self.last_observation > 250_000_000
        ):
            raise RuntimeError("perception progress expired")

    def finalize_run(self, outcome):
        self.cancel.set()
        if self.thread:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                raise RuntimeError("perception cleanup deadline expired")


def create_node(**kwargs):
    return PerceptionNode(**kwargs)


def main():
    spin(create_node)
