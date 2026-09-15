"""Run an explicitly admitted fixture through the eight ROS participants."""

import argparse
import json
import time
import uuid
from pathlib import Path

import rclpy
from alice_interfaces.action import RunSpeech
from rclpy.action import ActionClient
from rclpy.node import Node

from alice.contracts.speech_stream import SpeechClause
from alice.hardware.face_scope import face_manifest
from alice.hardware.manifest import load_manifest
from alice_nodes import contracts as wire
from alice_nodes.base import clock_proof, config_digest
from alice_nodes.transport import RunIdentity


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--fixtures-root", type=Path, default=Path("/fixtures"))
    parser.add_argument("--config-root", type=Path, default=Path("/opt/alice/config"))
    parser.add_argument(
        "--hardware-root", type=Path, default=Path("/opt/alice/hardware")
    )
    parser.add_argument("--profile", default="visible-face")
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--sad-hold-ms", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=90)
    options = parser.parse_args(args)
    if Path(options.fixture).name != options.fixture:
        parser.error("fixture must be one trusted basename")
    with (options.fixtures_root / options.fixture).open() as source:
        first = SpeechClause.model_validate_json(next(source))
    rclpy.init()
    node = Node("run_client", namespace="/alice")
    client = ActionClient(node, RunSpeech, "/alice/run_speech")
    identity = RunIdentity(
        "run-" + uuid.uuid4().hex, uuid.uuid4().hex, first.generation_id
    )
    incarnation = uuid.uuid4().hex
    accepted_identity = None
    session_incarnation = None
    samples = 0

    def feedback(message):
        nonlocal accepted_identity, session_incarnation, samples
        value = message.feedback
        if accepted_identity is None:
            accepted_identity = wire.run_identity_from_msg(value.header.identity)
            session_incarnation = value.header.publisher_incarnation
        wire.validate_run_speech_feedback(
            value,
            expected_identity=accepted_identity,
            expected_publisher_incarnation=session_incarnation,
        )
        samples = value.played_samples

    def wait(future):
        deadline = time.monotonic() + options.timeout
        while not future.done() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.05)
        if not future.done():
            raise RuntimeError("action deadline expired")
        return future.result()

    try:
        if not client.wait_for_server(timeout_sec=10):
            raise RuntimeError("session action unavailable")
        goal = RunSpeech.Goal(
            schema_version="run-speech/v1",
            identity=wire.run_identity_to_msg(identity),
            source=RunSpeech.Goal.FIXTURE,
            fixture_name=options.fixture,
            selected_profile=options.profile,
            seed=first.seed,
            hardware=options.hardware,
            sad_hold_ms=options.sad_hold_ms,
            config_sha256=config_digest(options.config_root, options.profile),
            calibration_sha256=face_manifest(
                load_manifest(options.hardware_root / "alice-face-v1.yaml")
            ).calibration_sha256,
            clock_domain_fingerprint=clock_proof(identity.epoch),
            requester_incarnation=incarnation,
        )
        handle = wait(client.send_goal_async(goal, feedback_callback=feedback))
        if not handle.accepted:
            raise RuntimeError("session rejected action admission")
        result = wait(handle.get_result_async()).result
        if accepted_identity is None:
            accepted_identity = wire.run_identity_from_msg(result.identity)
            session_incarnation = result.responder_incarnation
        value = wire.validate_run_speech_result(
            result,
            expected_identity=accepted_identity,
            expected_responder_incarnation=session_incarnation,
        )
        print(
            json.dumps(
                {
                    "identity": vars(value.identity),
                    "outcome": value.terminal_outcome,
                    "artifact": value.artifact_identity,
                    "played_samples": samples,
                    "error": value.error,
                }
            )
        )
        return 0 if value.terminal_outcome == "success" else 1
    finally:
        node.destroy_node()
        rclpy.shutdown()
