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


def test_shared_and_legacy_face_adapter_factories_are_callable():
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


@pytest.mark.parametrize("role", ["expression", "motion", "maestro"])
def test_first_control_deadline_starts_only_at_playing(role, tmp_path):
    import time

    import rclpy
    from alice_nodes.base import RuntimePaths
    from test_lifecycle import prepare, start

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
        request = prepare(node)
        assert node.begin(request).accepted
        node.start_run = lambda: None
        assert start(node, request).accepted
        now = time.monotonic_ns()
        node.check_progress(
            now + 1_000_000_000
        )  # No PLAYING: separate startup allowance.
        assert callable(getattr(node, "observe_playback", None)), (
            "independent PLAYING observer missing"
        )
        from alice_interfaces.msg import PlaybackStatus
        from alice_nodes.contracts import stream_header_to_msg
        from alice_nodes.transport import StreamHeader

        node.observe_playback(
            PlaybackStatus(
                header=stream_header_to_msg(
                    StreamHeader(node.identity, 0, now, "audio-incarnation")
                ),
                schema_version="playback-status/v1",
                state=PlaybackStatus.PLAYING,
                submitted_samples=480,
                played_samples=1,
            )
        )
        node.check_progress(now + 249_000_000)
        with pytest.raises(RuntimeError, match="control.*progress"):
            node.check_progress(now + 251_000_000)
        node._ended = "success"
        with pytest.raises(RuntimeError, match="control.*progress"):
            node.check_progress(now + 251_000_000)
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_external_clause_relay_preserves_source_and_rejects_aged_queue(tmp_path):
    import time

    import rclpy
    from alice_nodes.base import RuntimePaths
    from alice_nodes.contracts import speech_clause_to_msg
    from alice_nodes.session import create_node
    from alice_nodes.transport import SequenceGuard, StreamHeader
    from test_lifecycle import prepare, start

    from alice.contracts.speech_stream import ClauseSequence, SpeechClause

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
        request.requester_incarnation = node.incarnation
        assert node.begin(request).accepted
        assert start(node, request).accepted
        node.accept_external = True
        node.external_incarnation = "source"
        node.external_guard = SequenceGuard(node.identity, exact=True, max_gap_ns=None)
        node.external_ledger = ClauseSequence()
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
        stamp = time.monotonic_ns()
        node.external_clause(
            speech_clause_to_msg(
                clause, StreamHeader(node.identity, 0, stamp, "source")
            )
        )
        queued = node.external.get_nowait()
        assert callable(getattr(node, "relay_external", None)), (
            "source-preserving relay missing"
        )
        relayed = node.relay_external(queued, now_ns=stamp + 249_000_000)
        assert relayed.header.source_monotonic_ns == stamp
        with pytest.raises(ValueError, match="expired"):
            node.relay_external(queued, now_ns=stamp + 251_000_000)
        assert node.sequences["clauses"] == 1
    finally:
        node.destroy_node()
        rclpy.shutdown()


class StalledFirstChunkBackend:
    identity = {"test": "stalled-first-chunk"}

    def stream(self, text, *, voice, seed):
        import time

        time.sleep(60)
        yield  # Never emits before the owned process is terminated.


def test_cancel_stalled_first_tts_chunk_terminates_owned_process_and_writes_evidence(
    tmp_path,
):
    import time

    import rclpy
    from alice_interfaces.msg import RunHealth
    from alice_nodes.base import HEALTH, RuntimePaths
    from alice_nodes.contracts import stream_header_to_msg
    from alice_nodes.transport import StreamHeader
    from alice_nodes.tts import create_node
    from rclpy.executors import MultiThreadedExecutor
    from test_lifecycle import prepare, start

    from alice.contracts.speech_stream import SpeechClause
    from alice.speech.tts_worker import PocketTtsWorker

    rclpy.init()
    node = create_node(
        paths=RuntimePaths(
            Path("/workspace/config"),
            Path("/workspace/hardware"),
            tmp_path,
            Path("/workspace/config/speech"),
        )
    )
    observer = rclpy.create_node("tts_stall_health_probe")
    health = []
    observer.create_subscription(RunHealth, "/alice/run/health", health.append, HEALTH)
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    executor.add_node(observer)
    worker = PocketTtsWorker(backend=StalledFirstChunkBackend())
    try:
        request = prepare(node)
        assert node.begin(request).accepted
        assert start(node, request).accepted
        node.engine = worker
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
        node.submit(lambda: node.clause(clause))
        deadline = time.monotonic() + 2
        while not worker.is_alive and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.is_alive
        sequence = 0
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            for peer, incarnation in node.peers.items():
                if peer != node.role:
                    node.receive_health(
                        RunHealth(
                            header=stream_header_to_msg(
                                StreamHeader(
                                    node.identity,
                                    sequence,
                                    time.monotonic_ns(),
                                    incarnation,
                                )
                            ),
                            schema_version="run-health/v1",
                            state=RunHealth.ACTIVE,
                        )
                    )
            sequence += 1
            executor.spin_once(timeout_sec=0.01)
        assert node.error is None
        assert len(health) >= 3 and all(m.state == RunHealth.ACTIVE for m in health)
        node.fail("cancel during first chunk")
        deadline = time.monotonic() + 2
        while (
            worker.is_alive or not (node.local_dir / "terminal.json").exists()
        ) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not worker.is_alive, "owned inference process survived cancellation"
        assert (node.local_dir / "terminal.json").exists()
        assert node.ledger.sent_samples == 0
    finally:
        executor.shutdown(timeout_sec=1)
        observer.destroy_node()
        node.destroy_node()
        rclpy.shutdown()


def test_cancelled_queued_maestro_start_never_calls_adapter_factory(
    tmp_path, monkeypatch
):
    import threading
    import time

    import rclpy
    from alice_nodes.base import RuntimePaths
    from alice_nodes.maestro import create_node
    from test_lifecycle import prepare, start

    rclpy.init()
    node = create_node(
        paths=RuntimePaths(
            Path("/workspace/config"),
            Path("/workspace/hardware"),
            tmp_path,
            Path("/workspace/config/speech"),
        )
    )
    calls = []
    monkeypatch.setattr(
        "alice_nodes.maestro.face_factory", lambda *a, **kw: calls.append("factory")
    )
    entered, release = threading.Event(), threading.Event()
    try:
        request = prepare(node)
        assert node.begin(request).accepted
        node.submit(lambda: (entered.set(), release.wait(3)))
        assert entered.wait(1)
        starter = threading.Thread(target=lambda: start(node, request))
        starter.start()
        deadline = time.monotonic() + 1
        while not node._lifecycle_busy and time.monotonic() < deadline:
            time.sleep(0.005)
        node.fail("cancel before START executes")
        release.set()
        starter.join(2)
        assert calls == []
        deadline = time.monotonic() + 1
        while not node._completed and time.monotonic() < deadline:
            time.sleep(0.01)
        assert node.begin(prepare(node, "next-epoch")).accepted
        assert node.runtime is None and calls == []
    finally:
        release.set()
        node.destroy_node()
        rclpy.shutdown()


def test_blocked_expression_computation_faults_with_live_peers_and_cannot_publish_late(
    tmp_path,
):
    import threading
    import time

    import rclpy
    from alice_interfaces.msg import ExpressionFrame, PlaybackStatus, RunHealth
    from alice_nodes.base import HEALTH, LATEST, RuntimePaths
    from alice_nodes.contracts import speech_state_to_msg, stream_header_to_msg
    from alice_nodes.expression import create_node
    from alice_nodes.transport import StreamHeader
    from rclpy.executors import MultiThreadedExecutor
    from test_lifecycle import prepare, start

    from alice.speech.timeline import SpeechFrame

    rclpy.init()
    node = create_node(
        paths=RuntimePaths(
            Path("/workspace/config"),
            Path("/workspace/hardware"),
            tmp_path,
            Path("/workspace/config/speech"),
        )
    )
    observer = rclpy.create_node("stalled_expression_probe")
    health, proposals = [], []
    observer.create_subscription(RunHealth, "/alice/run/health", health.append, HEALTH)
    observer.create_subscription(
        ExpressionFrame, "/alice/expression/frame", proposals.append, LATEST
    )
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    executor.add_node(observer)
    entered, release = threading.Event(), threading.Event()
    try:
        request = prepare(node)
        assert node.begin(request).accepted
        assert start(node, request).accepted
        original = node.bridge.advance

        def stalled(*args):
            entered.set()
            release.wait(3)
            return original(*args)

        node.bridge.advance = stalled
        stamp = time.monotonic_ns()
        node.observe_playback(
            PlaybackStatus(
                header=stream_header_to_msg(
                    StreamHeader(node.identity, 0, stamp, "audio-incarnation")
                ),
                schema_version="playback-status/v1",
                state=PlaybackStatus.PLAYING,
                submitted_samples=480,
                played_samples=1,
            )
        )
        frame = SpeechFrame(
            sample_index=1,
            mouth_aperture=0.2,
            speech_weight=1.0,
            vector=(0, 0, 0),
            intensity=0.1,
        )
        message = speech_state_to_msg(
            frame,
            StreamHeader(node.identity, 0, stamp, "audio-incarnation"),
            sample_rate=24000,
            phase="playing",
            owner="audio-incarnation",
        )
        node.submit(lambda: node.speech(message))
        assert entered.wait(1)
        sequence = 0
        deadline = time.monotonic() + 0.7
        while time.monotonic() < deadline:
            now = time.monotonic_ns()
            for peer, incarnation in node.peers.items():
                if peer != node.role:
                    node.receive_health(
                        RunHealth(
                            header=stream_header_to_msg(
                                StreamHeader(node.identity, sequence, now, incarnation)
                            ),
                            schema_version="run-health/v1",
                            state=RunHealth.ACTIVE,
                        )
                    )
            sequence += 1
            executor.spin_once(timeout_sec=0.01)
        assert "control source progress" in node.error
        assert (node.local_dir / "terminal.json").exists()
        assert not node.begin(prepare(node, "next-epoch")).accepted
        assert len([m for m in health if m.state == RunHealth.ACTIVE]) >= 2
        assert any(m.state == RunHealth.FAULT for m in health)
        assert proposals == []
        release.set()
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.01)
        assert proposals == []
    finally:
        release.set()
        executor.shutdown(timeout_sec=1)
        observer.destroy_node()
        node.destroy_node()
        rclpy.shutdown()


def test_maestro_drain_cannot_authorize_home_before_first_control(tmp_path):
    from types import SimpleNamespace

    import rclpy
    from alice_nodes.base import RuntimePaths
    from alice_nodes.maestro import create_node
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
        node.playback = SimpleNamespace(
            drained=True,
            header=SimpleNamespace(identity=node.identity),
            error=None,
            response_final_seen=True,
        )
        with pytest.raises(RuntimeError, match="control"):
            node.require_drain()
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_recorder_admitted_writes_finish_before_success_closes_events(tmp_path):
    import json
    import threading
    import time

    import rclpy
    from alice_interfaces.msg import PlaybackStatus
    from alice_nodes.base import RuntimePaths, write_json
    from alice_nodes.contracts import stream_header_to_msg, validate_playback_status
    from alice_nodes.recorder import create_node
    from alice_nodes.transport import StreamHeader
    from test_lifecycle import prepare, start, successful_end

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
        for role in node.peers.keys() - {"recorder", "session"}:
            directory = node.run_dir / role
            directory.mkdir(exist_ok=True)
            write_json(
                directory / "terminal.json",
                {"identity": vars(node.identity), "outcome": "success"},
            )
        write_json(
            node.run_dir / "audio/audio.json",
            {"drained": True, "response_final": True, "underflows": 0},
        )
        write_json(
            node.run_dir / "maestro/servo.json",
            {"home_confirmed": True, "runtime_done": True, "error": None},
        )

        def event(sequence):
            return PlaybackStatus(
                header=stream_header_to_msg(
                    StreamHeader(
                        node.identity,
                        sequence,
                        time.monotonic_ns(),
                        "audio-incarnation",
                    )
                ),
                schema_version="playback-status/v1",
                state=PlaybackStatus.PLAYING,
                submitted_samples=480,
                played_samples=1,
            )

        def delayed_validate(message):
            validate_playback_status(message)
            entered.set()
            release.wait(2)

        first, second = event(0), event(1)
        node.submit(lambda: node.record(first, "audio", delayed_validate))
        assert entered.wait(1)
        node.submit(lambda: node.record(second, "audio", validate_playback_status))
        assert successful_end(node, request).accepted
        time.sleep(0.05)
        assert node.events is not None and not node.events.closed
        assert not node._completed and not (node.local_dir / "terminal.json").exists()
        release.set()
        deadline = time.monotonic() + 1
        while not node._completed and time.monotonic() < deadline:
            time.sleep(0.01)
        assert node._completed and node.error is None
        assert node.events is None
        rows = [
            json.loads(line)
            for line in (node.local_dir / "events.jsonl").read_text().splitlines()
        ]
        assert [row["message"]["header"]["sequence"] for row in rows] == [0, 1]
        assert (
            json.loads((node.local_dir / "manifest.json").read_text())["event_count"]
            == 2
        )
    finally:
        release.set()
        node.destroy_node()
        rclpy.shutdown()


def test_maestro_hardware_factory_rechecks_unavailable_host_visibility(monkeypatch):
    from types import SimpleNamespace

    from alice_nodes.maestro import MaestroNode

    calls = []
    monkeypatch.setattr(
        "alice_nodes.maestro.face_factory", lambda *a, **kw: calls.append("factory")
    )
    node = SimpleNamespace(binding=SimpleNamespace(hardware=True))
    with pytest.raises(ValueError, match="host FD visibility"):
        MaestroNode.start_run(node)
    assert not calls
