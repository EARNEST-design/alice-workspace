"""Integration boundaries: real domain components, generated ROS contracts."""

import importlib
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "role",
    [
        "session",
        "tts",
        "audio",
        "expression",
        "motion",
        "maestro",
        "perception",
        "recorder",
    ],
)
def test_all_runnable_nodes_start_idle_without_device_ownership(role, tmp_path):
    import rclpy

    assert importlib.util.find_spec(f"alice_nodes.{role}") is not None, (
        f"missing runtime {role}"
    )
    from alice_nodes.base import RuntimePaths

    rclpy.init()
    node = importlib.import_module(f"alice_nodes.{role}").create_node(
        paths=RuntimePaths(
            Path("/workspace/config"),
            Path("/workspace/hardware"),
            tmp_path,
            Path("/workspace/config/speech"),
        )
    )
    try:
        assert node.get_name() == role
        assert node.identity is None
        assert node.error is None
        assert not list(tmp_path.rglob("commands.jsonl"))
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_audio_tail_counts_never_acknowledge_unsent_transport_samples():
    assert importlib.util.find_spec("alice_nodes.audio") is not None, (
        "missing audio runtime"
    )
    from alice_nodes.audio import AudioCounts

    counts = AudioCounts(transport=480, generated=768, submitted=700, played=650)
    assert counts.consumed_transport == 480
    with pytest.raises(ValueError):
        AudioCounts(transport=480, generated=768, submitted=700, played=701)


def test_maestro_refuses_home_without_current_successful_drain(tmp_path):
    import rclpy

    assert importlib.util.find_spec("alice_nodes.maestro") is not None, (
        "missing maestro runtime"
    )
    from alice_nodes.base import RuntimePaths
    from alice_nodes.maestro import create_node

    rclpy.init()
    node = create_node(
        paths=RuntimePaths(
            Path("/workspace/config"),
            Path("/workspace/hardware"),
            tmp_path,
            Path("/workspace/config/speech"),
        )
    )
    try:
        with pytest.raises(RuntimeError, match="drain"):
            node.require_drain()
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_shared_face_adapter_is_lightweight_and_legacy_compatible():
    assert importlib.util.find_spec("alice.speech.face_adapter") is not None, (
        "missing shared face adapter"
    )
    from alice.experiments.face_speech_cli import _factory
    from alice.speech.face_adapter import face_factory

    assert callable(face_factory) and callable(_factory)


def test_session_feedback_reports_generated_audio_tail_from_audio_health(tmp_path):
    from types import SimpleNamespace

    import rclpy
    from alice_nodes.base import RuntimePaths
    from alice_nodes.session import create_node
    from alice_nodes.transport import RunIdentity

    rclpy.init()
    node = create_node(
        paths=RuntimePaths(
            Path("/workspace/config"),
            Path("/workspace/hardware"),
            tmp_path,
            Path("/workspace/config/speech"),
        )
    )
    try:
        node.identity = RunIdentity("run", "epoch", "generation")
        node.committed = 2
        node.peer_health = {"audio": (0, SimpleNamespace(progress=31200))}
        node.playback_value = SimpleNamespace(
            submitted_samples=960, played_samples=480, drained=False, state="playing"
        )
        values = []
        node.feedback(SimpleNamespace(publish_feedback=values.append))
        assert values[0].generated_samples == 31200
        assert values[0].submitted_samples == 960 and values[0].played_samples == 480
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_tts_commits_metadata_on_receipt_before_backpressured_generation(tmp_path):
    import threading
    import time

    import rclpy
    from alice_nodes.base import RuntimePaths
    from alice_nodes.contracts import speech_clause_to_msg
    from alice_nodes.transport import StreamHeader
    from alice_nodes.tts import create_node
    from test_lifecycle import prepare, start

    from alice.contracts.speech_stream import SpeechClause

    rclpy.init()
    node = create_node(
        paths=RuntimePaths(
            Path("/workspace/config"),
            Path("/workspace/hardware"),
            tmp_path,
            Path("/workspace/config/speech"),
        )
    )
    entered, release = threading.Event(), threading.Event()
    try:
        request = prepare(node)
        assert node.begin(request).accepted
        assert start(node, request).accepted

        def blocked():
            entered.set()
            release.wait(2)

        node.submit(blocked)
        assert entered.wait(1)
        clause = SpeechClause(
            generation_id="generation",
            clause_id="one",
            sequence=0,
            text="Hello",
            vector=(0, 0, 0),
            intensity=0,
            seed=29,
            end_of_response=True,
        )
        message = speech_clause_to_msg(
            clause,
            StreamHeader(node.identity, 0, time.monotonic_ns(), "session-incarnation"),
        )
        node.receive_clause(message)
        node.clauses.finish()  # Source finality committed while generation is blocked.
        assert node.ledger.sent_samples == 0
    finally:
        release.set()
        node.destroy_node()
        rclpy.shutdown()


def test_stalled_dac_polling_cannot_refresh_local_progress(tmp_path):
    import rclpy
    from alice_nodes.audio import create_node
    from alice_nodes.base import RuntimePaths
    from test_lifecycle import prepare

    rclpy.init()
    node = create_node(
        paths=RuntimePaths(
            Path("/workspace/config"),
            Path("/workspace/hardware"),
            tmp_path,
            Path("/workspace/config/speech"),
        )
    )
    try:
        assert node.begin(prepare(node)).accepted
        node.playing = True
        node.player.sample_position = lambda _: 0  # A stalled external DAC clock.
        node._emit_clock(0.0)
        original = node.last_dac_ns
        node._emit_clock(0.0)
        assert node.last_dac_ns == original
        with pytest.raises(RuntimeError, match="DAC progress"):
            node.check_progress(original + 251_000_000)
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_action_cancellation_immediately_revokes_session(tmp_path):
    import rclpy
    from alice_nodes.base import RuntimePaths
    from alice_nodes.session import create_node
    from test_lifecycle import prepare

    rclpy.init()
    node = create_node(
        paths=RuntimePaths(
            Path("/workspace/config"),
            Path("/workspace/hardware"),
            tmp_path,
            Path("/workspace/config/speech"),
        )
    )
    try:
        assert node.begin(prepare(node)).accepted
        node.cancel_goal(None)
        assert node.cancel.is_set()
        assert node.error is not None
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_total_audio_budget_reserves_tail_at_exact_boundary():
    from alice_nodes.audio import validate_audio_budget

    validate_audio_budget(232800, tail_samples=7200, sample_rate=24000)
    with pytest.raises(ValueError, match="ten-second"):
        validate_audio_budget(232801, tail_samples=7200, sample_rate=24000)


def test_over_budget_pcm_is_rejected_before_ring_or_timeline_mutates(tmp_path):
    import time

    import rclpy
    from alice_nodes.audio import create_node
    from alice_nodes.base import RuntimePaths
    from alice_nodes.contracts import pcm_packet_to_msg
    from alice_nodes.transport import PcmPacket, StreamHeader
    from test_lifecycle import prepare, start

    from alice.contracts.speech_stream import SpeechClause

    rclpy.init()
    node = create_node(
        paths=RuntimePaths(
            Path("/workspace/config"),
            Path("/workspace/hardware"),
            tmp_path,
            Path("/workspace/config/speech"),
        )
    )
    try:
        request = prepare(node)
        assert node.begin(request).accepted
        # Test ingress independently of the external device driver.
        node.start_run = lambda: None
        assert start(node, request).accepted
        clause = SpeechClause(
            generation_id="generation",
            clause_id="one",
            sequence=0,
            text="Hello",
            vector=(0, 0, 0),
            intensity=0,
            seed=29,
            end_of_response=True,
        )
        packet = PcmPacket(
            StreamHeader(node.identity, 0, time.monotonic_ns(), "tts-incarnation"),
            "one",
            0,
            232800,
            24000,
            (0.1,),
            True,
            clause,
            True,
            True,
        )
        with pytest.raises(ValueError, match="ten-second"):
            node.pcm(pcm_packet_to_msg(packet))
        assert node.ring.depth == 0
        assert node.pcm_guard.sample_offset == 0
        assert node.timeline.generated_samples == 0
        assert node.player.submitted_samples == 0
    finally:
        node.destroy_node()
        rclpy.shutdown()
