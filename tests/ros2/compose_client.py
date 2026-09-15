"""Action and DDS observer inside the tools container on the actual bridge."""

import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path

import rclpy
from alice_interfaces.action import RunSpeech
from alice_interfaces.msg import ExpressionFrame, PcmChunk, PlaybackStatus, RunHealth
from alice_interfaces.msg import SpeechClause as ClauseMessage
from alice_interfaces.srv import BeginRun, EndRun
from alice_nodes import contracts as wire
from alice_nodes.base import LATEST, clock_proof, config_digest
from alice_nodes.transport import RunIdentity, StreamHeader
from rclpy.action import ActionClient
from rclpy.node import Node
from std_srvs.srv import Trigger

from alice.contracts.speech_stream import SpeechClause
from alice.hardware.face_scope import face_manifest
from alice.hardware.manifest import load_manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("--fixture", default="stream-visible-demo-v1.jsonl")
    options = parser.parse_args()
    output = Path("/artifacts")
    rclpy.init()
    node = Node("compose_qualification_client")
    health, playback, pcm = [], [], []
    packets, expressions, feedback = [], [], []
    node.create_subscription(PcmChunk, "/alice/speech/pcm", packets.append, 128)
    node.create_subscription(
        ExpressionFrame, "/alice/expression/frame", expressions.append, LATEST
    )
    external = node.create_publisher(ClauseMessage, "/alice/run/committed_clauses", 32)
    origin = {"monotonic_ns": time.monotonic_ns(), "wall_time_ns": time.time_ns()}
    node.create_subscription(
        PcmChunk,
        "/alice/speech/pcm",
        lambda m: pcm.append(
            {
                "source_ns": m.header.source_monotonic_ns,
                "observed_ns": time.monotonic_ns(),
                "samples": len(m.samples),
                "offset": m.global_sample_offset,
            }
        ),
        128,
    )
    node.create_subscription(
        RunHealth,
        "/alice/run/health",
        lambda m: health.append(
            {
                "source_ns": m.header.source_monotonic_ns,
                "state": m.state,
                "incarnation": m.header.publisher_incarnation,
            }
        ),
        64,
    )

    def status(message):
        playback.append(
            {
                "source_ns": message.header.source_monotonic_ns,
                "state": message.state,
                "played": message.played_samples,
            }
        )
        if (
            message.state == PlaybackStatus.PLAYING
            and not (output / "playing.json").exists()
        ):
            (output / "playing.json").write_text(
                json.dumps(
                    {
                        "observed_ns": time.monotonic_ns(),
                        "source_ns": message.header.source_monotonic_ns,
                    }
                )
            )

    node.create_subscription(
        PlaybackStatus, "/alice/audio/playback_status", status, 128
    )
    action = ActionClient(node, RunSpeech, "/alice/run_speech")
    try:
        deadline = time.monotonic() + 25
        names = set()
        while time.monotonic() < deadline:
            names = {
                name
                for name, ns in node.get_node_names_and_namespaces()
                if ns == "/alice"
            }
            if (
                set(
                    (
                        "session tts audio expression motion maestro "
                        "perception recorder"
                    ).split()
                )
                <= names
            ):
                break
            rclpy.spin_once(node, timeout_sec=0.05)
        assert len(names) == 8, names
        assert not list(output.glob("*/session/terminal.json")), (
            "default boot was not idle"
        )
        (output / "graph.json").write_text(json.dumps({"nodes": sorted(names)}))
        assert action.wait_for_server(timeout_sec=10)
        first = SpeechClause.model_validate_json(
            (Path("/fixtures") / options.fixture).read_text().splitlines()[0]
        )
        identity = RunIdentity(
            "compose-" + uuid.uuid4().hex, uuid.uuid4().hex, first.generation_id
        )
        goal = RunSpeech.Goal(
            schema_version="run-speech/v1",
            identity=wire.run_identity_to_msg(identity),
            source=RunSpeech.Goal.FIXTURE,
            fixture_name=options.fixture,
            selected_profile="visible-face",
            seed=first.seed,
            hardware=False,
            sad_hold_ms=1500 if options.scenario == "cancel-hold" else 0,
            config_sha256=config_digest(Path("/opt/alice/config"), "visible-face"),
            calibration_sha256=face_manifest(
                load_manifest(Path("/opt/alice/hardware/alice-face-v1.yaml"))
            ).calibration_sha256,
            clock_domain_fingerprint=clock_proof(identity.epoch),
            requester_incarnation=uuid.uuid4().hex,
        )
        if options.scenario == "config-mismatch":
            goal.config_sha256 = "0" * 64
        if options.scenario == "clock-mismatch":
            goal.clock_domain_fingerprint = "0" * 64
        if options.scenario == "external-relay-aging":
            goal.source = RunSpeech.Goal.COMMITTED_CLAUSES
            goal.fixture_name = ""

        def wait(future, seconds=65):
            end = time.monotonic() + seconds
            while not future.done() and time.monotonic() < end:
                rclpy.spin_once(node, timeout_sec=0.01)
            assert future.done(), "bounded action timeout"
            return future.result()

        handle = wait(
            action.send_goal_async(
                goal, feedback_callback=lambda m: feedback.append(m.feedback)
            )
        )
        assert handle.accepted
        future = handle.get_result_async()
        cancelled = False
        playing_at = None
        external_sent = False
        crash_requested = False
        crash = node.create_client(Trigger, "/qualification/crash")
        if options.scenario.startswith("selfkill-"):
            assert crash.wait_for_service(timeout_sec=5)
        terminal_before_retirement = False
        deadline = time.monotonic() + 70
        while not future.done() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.005)
            if playing_at is None and any(
                p["state"] == PlaybackStatus.PLAYING for p in playback
            ):
                playing_at = time.monotonic()
            if (
                options.scenario == "external-relay-aging"
                and feedback
                and not external_sent
            ):
                external.publish(
                    wire.speech_clause_to_msg(
                        first,
                        StreamHeader(
                            identity=wire.run_identity_from_msg(
                                feedback[0].header.identity
                            ),
                            sequence=0,
                            source_monotonic_ns=time.monotonic_ns(),
                            publisher_incarnation=goal.requester_incarnation,
                        ),
                    )
                )
                external_sent = True
            if options.scenario == "expression-stall-cancel":
                terminal_before_retirement |= (
                    bool(list(output.glob("*/expression/terminal.json")))
                    and not (output / "expression-stall-retired.json").exists()
                )
            if (
                options.scenario.startswith("selfkill-")
                and playing_at is not None
                and not crash_requested
            ):
                crash_requested = True
                crash.call_async(Trigger.Request())
            should_cancel = (
                (
                    options.scenario == "backpressure"
                    and playing_at is not None
                    and time.monotonic() - playing_at > 1
                )
                or (
                    options.scenario in {"cancel", "terminal-conflict"}
                    and any(p["state"] == PlaybackStatus.PLAYING for p in playback)
                )
                or (
                    options.scenario == "cancel-hold"
                    and any(p["state"] == PlaybackStatus.DRAINED for p in playback)
                )
            )
            should_cancel |= (
                options.scenario in {"tts-stall-cancel", "expression-stall-cancel"}
                and (
                    output
                    / (options.scenario.removesuffix("-cancel") + "-entered.json")
                ).exists()
            )
            if should_cancel and not cancelled:
                cancelled = True
                (output / "cancel.json").write_text(
                    json.dumps({"requested_ns": time.monotonic_ns()})
                )
                handle.cancel_goal_async()
            if options.scenario in {
                "kill-session",
                "sigterm-all",
                "selfkill-session",
            } and (
                (output / "injection.json").exists()
                or (output / "selfkill-marker.json").exists()
            ):
                time.sleep(1)
                break
        if future.done():
            result = future.result().result
            value = {
                "outcome": {0: "success", 1: "cancelled", 2: "fault"}[
                    result.terminal_outcome
                ],
                "error": result.error,
                "epoch": result.identity.epoch,
                "artifact": result.artifact_identity,
            }
        else:
            assert options.scenario in {
                "kill-session",
                "sigterm-all",
                "selfkill-session",
            }
            value = {
                "outcome": "sigterm-stop"
                if options.scenario == "sigterm-all"
                else "session-killed",
                "error": "owner unavailable, local evidence required",
            }
        if options.scenario.startswith("selfkill-"):
            assert crash_requested and (output / "selfkill-marker.json").exists()
            assert not (output / "selfkill-rejected.json").exists(), (
                "marker write exceeded its bound; no crash qualification"
            )
        if options.scenario == "external-relay-aging":
            assert external_sent and not pcm and "expired at relay" in value["error"], (
                value
            )
            evidence = json.loads((output / "relay-aged.json").read_text())
            assert evidence["source_unchanged"]
            assert (
                evidence["forward_attempt_ns"] - evidence["original_ns"] > 250_000_000
            )
        if options.scenario == "tts-stall-cancel":
            assert cancelled and not pcm
            worker = json.loads(
                next(output.glob("*/tts/qualification-owned-worker.json")).read_text()
            )
            assert not worker["alive"], worker
        if options.scenario == "expression-stall-cancel":
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=0.01)
            assert cancelled and terminal_before_retirement and not expressions
            (output / "blocked-cancellation.json").write_text(
                json.dumps(
                    {
                        "terminal_before_retirement": True,
                        "late_expression_frames": len(expressions),
                    }
                )
            )
        if options.scenario in {"terminal-conflict", "stale-epoch"}:
            role = "maestro" if options.scenario == "terminal-conflict" else "audio"
            terminal_path = next(output.glob(f"*/{role}/terminal.json"))
            prior = json.loads(terminal_path.read_text())
            prior_hash = hashlib.sha256(terminal_path.read_bytes()).hexdigest()
            session = json.loads(
                next(output.glob("*/session/terminal.json")).read_text()
            )
            end = node.create_client(EndRun, f"/alice/{role}/end_run")
            assert end.wait_for_service(timeout_sec=5)
            if options.scenario == "terminal-conflict":
                responses = []
                for outcome in [
                    EndRun.Request.FAULT,
                    EndRun.Request.FAULT,
                    EndRun.Request.SUCCESS,
                    EndRun.Request.SUCCESS,
                ]:
                    reply = wait(
                        end.call_async(
                            EndRun.Request(
                                schema_version="end-run/v1",
                                identity=wire.run_identity_to_msg(
                                    RunIdentity(**prior["identity"])
                                ),
                                outcome=outcome,
                                reason="repeat terminal qualification",
                                requester_incarnation=session["incarnation"],
                            )
                        )
                    )
                    assert reply.accepted == (outcome == EndRun.Request.FAULT), (
                        reply.error
                    )
                    if outcome == EndRun.Request.FAULT:
                        assert reply.idempotent and reply.completed
                    responses.append(
                        {
                            "outcome": outcome,
                            "accepted": reply.accepted,
                            "idempotent": reply.idempotent,
                            "error": reply.error,
                        }
                    )
                (output / "terminal-conflict.json").write_text(
                    json.dumps(responses, indent=2)
                )
            else:
                assert packets
                begin = node.create_client(BeginRun, "/alice/audio/begin_run")
                assert begin.wait_for_service(timeout_sec=5)
                epoch = uuid.uuid4().hex
                fresh = RunIdentity(identity.run_id, epoch, identity.generation_id)
                request = BeginRun.Request(
                    schema_version="begin-run/v1",
                    identity=wire.run_identity_to_msg(fresh),
                    operation=BeginRun.Request.PREPARE,
                    selected_profile=goal.selected_profile,
                    seed=goal.seed,
                    hardware=False,
                    sad_hold_ms=0,
                    config_sha256=goal.config_sha256,
                    calibration_sha256=goal.calibration_sha256,
                    clock_domain_fingerprint=clock_proof(epoch),
                    requester_incarnation="stale-epoch-probe",
                )
                reply = wait(begin.call_async(request))
                assert reply.accepted, reply.error
                publisher = node.create_publisher(PcmChunk, "/alice/speech/pcm", 32)
                until = time.monotonic() + 0.5
                while time.monotonic() < until:
                    rclpy.spin_once(node, timeout_sec=0.01)
                publisher.publish(packets[0])
                until = time.monotonic() + 0.2
                while time.monotonic() < until:
                    rclpy.spin_once(node, timeout_sec=0.01)
                reply = wait(
                    end.call_async(
                        EndRun.Request(
                            schema_version="end-run/v1",
                            identity=request.identity,
                            outcome=EndRun.Request.CANCELLED,
                            reason="qualification complete",
                            requester_incarnation="stale-epoch-probe",
                        )
                    )
                )
                assert reply.accepted, reply.error
                fresh_dir = (
                    output / hashlib.sha256(epoch.encode()).hexdigest()[:24] / "audio"
                )
                until = time.monotonic() + 3
                while (
                    not (fresh_dir / "terminal.json").exists()
                    and time.monotonic() < until
                ):
                    rclpy.spin_once(node, timeout_sec=0.01)
                audio = json.loads((fresh_dir / "audio.json").read_text())
                assert (
                    audio["transport"]
                    == audio["generated"]
                    == audio["submitted"]
                    == audio["played"]
                    == 0
                ), audio
                (output / "stale-epoch.json").write_text(
                    json.dumps(
                        {
                            "new_epoch_prepared": True,
                            "old_packet_delivered": True,
                            "new_epoch_transport_samples": 0,
                        }
                    )
                )
            assert hashlib.sha256(terminal_path.read_bytes()).hexdigest() == prior_hash
        (output / "client.json").write_text(json.dumps(value, indent=2))
        (output / "observer.json").write_text(
            json.dumps(
                {
                    "health": health,
                    "playback": playback,
                    "pcm": pcm,
                    "diagnostic_clock_origin": origin,
                },
                indent=2,
            )
        )
        print(json.dumps(value), flush=True)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
