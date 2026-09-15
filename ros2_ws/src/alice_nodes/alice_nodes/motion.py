"""Compose the expression with its exact embedded speech frame."""

from alice_interfaces.msg import ExpressionFrame, FaceTarget

from alice.contracts.speech import SpeechSyncConfig
from alice.speech.composer import compose_frame
from alice_nodes import contracts as wire
from alice_nodes.base import LATEST, RuntimeNode, spin


class MotionNode(RuntimeNode):
    def __init__(self, **kwargs):
        super().__init__("motion", **kwargs)
        self.publisher = self.create_publisher(FaceTarget, "/alice/face/target", LATEST)
        self.subscribe_work(
            ExpressionFrame, "/alice/expression/frame", self.expression, latest=True
        )

    def prepare_run(self):
        self.sync = SpeechSyncConfig.model_validate_json(
            (self.config_root / "speech/sync-hardware-v1.json").read_text()
        )

    def expression(self, message):
        header = wire.stream_header_from_msg(message.header)
        if not self.admit_header(header, "expression", "expression"):
            return
        expression = wire.expression_frame_from_msg(
            message, expected_calibration_sha256=self.calibration
        )
        speech = message.speech_state
        if (
            speech.header.publisher_incarnation != self.peers["audio"]
            or speech.owner_incarnation != self.peers["audio"]
        ):
            raise ValueError("embedded speech publisher incarnation mismatch")
        frame = wire.speech_state_from_msg(speech)
        proposal = compose_frame(expression, frame, self.sync)
        self.publisher.publish(
            wire.face_target_to_msg(
                proposal,
                self.header("target", header.source_monotonic_ns),
                config_sha256=self.binding.config_sha256,
                calibration_sha256=self.calibration,
                speech_sequence=speech.header.sequence,
                played_sample=frame.sample_index,
            )
        )
        self.progress += 1


def create_node(**kwargs):
    return MotionNode(**kwargs)


def main():
    spin(create_node)
