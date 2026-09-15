"""Exclusive selected-face owner; success requires matched DAC drain and Home."""

import json
import threading

from alice_interfaces.msg import (
    FaceTarget,
    PlaybackStatus,
    ServoChannelReceipt,
    ServoReceipt,
)

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.hardware.manifest import load_manifest
from alice.speech.face_adapter import face_factory
from alice.speech.face_runtime import FaceRuntime
from alice_nodes import contracts as wire
from alice_nodes.base import (
    RELIABLE,
    RuntimeNode,
    require_ros_hardware_visibility,
    spin,
    write_json,
)


class MaestroNode(RuntimeNode):
    def __init__(self, **kwargs):
        super().__init__("maestro", **kwargs)
        self.runtime = None
        self.receipt_lock = threading.Lock()
        self.playback = None
        self.publisher = self.create_publisher(
            ServoReceipt, "/alice/maestro/servo_receipt", RELIABLE
        )
        self.subscribe_work(FaceTarget, "/alice/face/target", self.target, latest=True)
        self.create_subscription(
            PlaybackStatus,
            "/alice/audio/playback_status",
            self.status,
            RELIABLE,
            callback_group=self.group,
        )
        self.create_timer(0.02, self.queue_receipts, callback_group=self.group)

    def prepare_run(self):
        self.full = load_manifest(self.paths.hardware / "alice-face-v1.yaml")
        self.playback = None
        self.record_index = 0
        self.runtime = None

    def start_run(self):
        if self.binding.hardware:
            require_ros_hardware_visibility()
        with self._lock:
            if self.cancel.is_set():
                raise RuntimeError("START cancelled before adapter creation")
            self.runtime = FaceRuntime(
                face_factory(
                    self.full,
                    self.identity.generation_id,
                    self.binding.hardware,
                    {},
                    self.local_dir,
                    config_root=self.config_root,
                ),
                on_fault=lambda: self.fail("face runtime fault"),
            )
            self.runtime.start()
        if not self.runtime.ready.wait(5):
            raise RuntimeError("face startup deadline expired")
        self.runtime.raise_if_failed()

    def target(self, message):
        header = wire.stream_header_from_msg(message.header)
        if not self.admit_header(header, "motion", "target"):
            return
        proposal = wire.face_target_from_msg(
            message,
            expected_config_sha256=self.binding.config_sha256,
            expected_calibration_sha256=self.calibration,
        )
        if self.playback and self.playback.drained:
            return
        self.runtime.offer(
            proposal,
            self.identity.generation_id,
            message.played_sample,
            source_monotonic_ns=header.source_monotonic_ns,
        )
        self._control_source = header.source_monotonic_ns
        self.progress += 1

    def status(self, message):
        if not self.current(message) or not self.peers or self.error:
            return
        try:
            header = wire.stream_header_from_msg(message.header)
            if self.admit_header(header, "audio", "playback"):
                value = wire.validate_playback_status(message)
                if self.playback and (
                    value.played_samples < self.playback.played_samples
                    or value.submitted_samples < self.playback.submitted_samples
                ):
                    raise ValueError("playback cumulative counts moved backward")
                self.playback = value
                if value.state in {"fault", "cancelled"}:
                    self.fail(value.error or "audio cancelled")
        except Exception as exc:
            self.fail(str(exc))

    def require_drain(self):
        if self._control_source is None:
            raise RuntimeError("successful drain requires first control progress")
        if (
            not self.playback
            or not self.playback.drained
            or self.playback.header.identity != self.identity
            or self.playback.error
            or not self.playback.response_final_seen
        ):
            raise RuntimeError("successful current-run PCM drain required before Home")

    def validate_end(self, outcome):
        if outcome == "success":
            self.require_drain()

    def queue_receipts(self):
        if self.runtime and self.runtime.stream and not self._completed:
            self.receipts()

    def receipts(self):
        with self.receipt_lock:
            self._publish_receipts()

    def _publish_receipts(self):
        if not self.runtime or not self.runtime.stream:
            return
        records = self.runtime.stream.records
        while self.record_index < len(records):
            row = records[self.record_index]
            # Serial owner appends a request before the status is available.
            if "request" in row and "status" not in row:
                return
            index = self.record_index
            self.record_index += 1
            if "status" not in row:
                continue
            target = row["request"]["targets"][0]
            definition = self.full.actuator(target["actuator_name"])
            status = row["status"]
            self.publisher.publish(
                ServoReceipt(
                    header=wire.stream_header_to_msg(
                        self.header("receipt", status["reported_monotonic_ns"])
                    ),
                    schema_version="servo-receipt/v1",
                    request_id=f"face-{index}",
                    hardware_id=self.full.hardware_id,
                    calibration_sha256=self.calibration,
                    state=ServoReceipt.APPLIED,
                    channels=[
                        ServoChannelReceipt(
                            actuator_name=target["actuator_name"],
                            channel=definition.channel,
                            target_qus=status["target_qus"],
                            observed_qus=status["observed_qus"],
                        )
                    ],
                )
            )

    def stop_local(self):
        if self.runtime:
            # Revocation is immediate; serial-owner cleanup never grants Home.
            self.runtime.cancel_signal.set()

    def check_progress(self, now):
        if self.runtime and self.runtime.error:
            raise RuntimeError(str(self.runtime.error))
        super().check_progress(now)

    def finalize_run(self, outcome):
        if self.runtime:
            if outcome == "success":
                self.require_drain()
                hold = self.binding.sad_hold_ms / 1000
                pose = (
                    TargetUpdate(
                        offset_s=0,
                        targets=(
                            ActuatorTarget(
                                actuator_name="mouth_open", normalized_position=-1
                            ),
                            ActuatorTarget(
                                actuator_name="left_mouth_corner",
                                normalized_position=-0.8,
                            ),
                            ActuatorTarget(
                                actuator_name="right_mouth_corner",
                                normalized_position=0.8,
                            ),
                        ),
                    )
                    if hold
                    else None
                )
                self.runtime.complete(hold_pose=pose, hold_s=hold)
                if not self.runtime.done.wait(14):
                    raise RuntimeError("face finalization deadline expired")
                self.runtime.raise_if_failed()
            else:
                self.runtime.abort(self.error or outcome)
            self.runtime.join()
            self.receipts()
        stream = self.runtime.stream if self.runtime else None
        with (self.local_dir / "commands.jsonl").open("w") as out:
            for row in stream.records if stream else []:
                out.write(json.dumps(row) + "\n")
        write_json(
            self.local_dir / "servo.json",
            {
                "runtime_done": bool(self.runtime and self.runtime.done.is_set()),
                "home_confirmed": bool(stream and stream.home_confirmed),
                "error": str(self.runtime.error)
                if self.runtime and self.runtime.error
                else None,
                "physical_motion_verified": False,
            },
        )


def create_node(**kwargs):
    return MaestroNode(**kwargs)


def main():
    spin(create_node)
