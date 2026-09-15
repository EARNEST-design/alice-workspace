"""Derived event recorder with durable local audio/servo corroboration."""

import hashlib
import json
import os

from alice_interfaces.msg import FaceObservation, PlaybackStatus, ServoReceipt

from alice_nodes import contracts as wire
from alice_nodes.base import RELIABLE, RuntimeNode, spin, write_json


class RecorderNode(RuntimeNode):
    def __init__(self, **kwargs):
        super().__init__("recorder", **kwargs)
        for msg, topic, producer, validate in [
            (
                FaceObservation,
                "/alice/perception/face_observation",
                "perception",
                wire.face_observation_from_msg,
            ),
            (
                ServoReceipt,
                "/alice/maestro/servo_receipt",
                "maestro",
                lambda m: wire.validate_servo_receipt(
                    m, expected_calibration_sha256=self.calibration
                ),
            ),
            (
                PlaybackStatus,
                "/alice/audio/playback_status",
                "audio",
                wire.validate_playback_status,
            ),
        ]:
            self.subscribe_work(
                msg,
                topic,
                lambda m, p=producer, v=validate: self.record(m, p, v),
                latest=producer == "perception",
                qos=RELIABLE if producer != "perception" else None,
            )
        self.events = None

    def prepare_run(self):
        self.event_count = 0
        self.events = (self.local_dir / "events.jsonl").open("x")

    def record(self, message, producer, validate):
        header = wire.stream_header_from_msg(message.header)
        if not self.admit_header(
            header, producer, "record:" + producer, sparse=producer == "maestro"
        ):
            return
        validate(message)
        if self.event_count >= 10000:
            raise RuntimeError("derived event bound exceeded")
        from rosidl_runtime_py.convert import message_to_ordereddict

        self.events.write(
            json.dumps(
                {"producer": producer, "message": message_to_ordereddict(message)}
            )
            + "\n"
        )
        self.event_count += 1
        self.progress += 1

    def finalize_run(self, outcome):
        if self.events:
            self.events.flush()
            os.fsync(self.events.fileno())
            self.events.close()
            self.events = None
        if outcome == "success":
            audio = json.loads((self.run_dir / "audio/audio.json").read_text())
            servo = json.loads((self.run_dir / "maestro/servo.json").read_text())
            if (
                not audio["drained"]
                or not audio["response_final"]
                or audio["underflows"]
                or not servo["home_confirmed"]
                or not servo["runtime_done"]
                or servo["error"]
            ):
                raise RuntimeError(
                    "local audio/servo evidence does not corroborate success"
                )
            for role in self.peers.keys() - {"recorder", "session"}:
                terminal = json.loads(
                    (self.run_dir / role / "terminal.json").read_text()
                )
                if (
                    terminal["identity"] != vars(self.identity)
                    or terminal["outcome"] != "success"
                ):
                    raise RuntimeError(
                        "missing or wrong-run durable participant evidence"
                    )
        manifest = {
            str(p.relative_to(self.run_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(self.run_dir.rglob("*"))
            if p.is_file() and p.suffix != ".tmp"
        }
        write_json(
            self.local_dir / "manifest.json",
            {
                "identity": vars(self.identity),
                "outcome": outcome,
                "files": manifest,
                "event_count": self.event_count,
                "telemetry_complete": False,
                "physical_motion_verified": False,
            },
        )


def create_node(**kwargs):
    return RecorderNode(**kwargs)


def main():
    spin(create_node)
