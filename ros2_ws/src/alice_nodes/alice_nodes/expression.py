"""Persistent expression inference driven only by the embedded audible frame."""

import time

from alice_interfaces.msg import ExpressionFrame, SpeechState

from alice_nodes import contracts as wire
from alice_nodes.base import LATEST, RuntimeNode, spin, write_json


class ExpressionNode(RuntimeNode):
    def __init__(self, **kwargs):
        super().__init__("expression", **kwargs)
        self.bridge = None
        self.publisher = self.create_publisher(
            ExpressionFrame, "/alice/expression/frame", LATEST
        )
        self.subscribe_work(
            SpeechState, "/alice/speech/state", self.speech, latest=True
        )

    def prepare_run(self):
        from alice.speech.expression_bridge import ExpressionBridge

        self.bridge = ExpressionBridge(
            config_root=self.config_root,
            seed=self.binding.seed,
            generation_id=self.identity.generation_id,
            mode="authored",
            head_enabled=False,
        )
        self.last_source = None
        write_json(self.local_dir / "model.json", self.bridge.identity)

    def speech(self, message):
        header = wire.stream_header_from_msg(message.header)
        if not self.admit_header(header, "audio", "speech"):
            return
        frame = wire.speech_state_from_msg(message)
        if message.owner_incarnation != self.peers["audio"]:
            raise ValueError("speech owner incarnation mismatch")
        if message.phase != SpeechState.PLAYING or not frame.speech_weight:
            return
        self.last_source = header.source_monotonic_ns
        expression = self.bridge.advance(frame, frame.sample_index, message.sample_rate)
        # Inference latency never turns an old source into a fresh proposal.
        if time.monotonic_ns() - header.source_monotonic_ns > 250_000_000:
            raise RuntimeError("expression inference expired original source")
        self.publisher.publish(
            wire.expression_frame_to_msg(
                expression,
                self.header("expression", header.source_monotonic_ns),
                message,
                calibration_sha256=self.calibration,
            )
        )
        self.progress += 1

    def finalize_run(self, outcome):
        if self.bridge:
            (self.local_dir / "state.json").write_text(self.bridge.snapshot())
            self.bridge.cancel()


def create_node(**kwargs):
    return ExpressionNode(**kwargs)


def main():
    spin(create_node)
