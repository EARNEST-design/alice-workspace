"""Final cross-component contract regressions, without physical devices."""

import asyncio
import hashlib
import json
import sys
import threading
import time
from types import MethodType, SimpleNamespace

import numpy as np
import pytest


@pytest.mark.parametrize("stage", ["prepare", "start", "playback", "finalize", "fault"])
def test_admitted_action_terminal_result_obeys_contract(stage, tmp_path):
    from alice_interfaces.action import RunSpeech
    from alice_interfaces.srv import BeginRun
    from alice_nodes import contracts as wire
    from alice_nodes.session import SessionNode
    from alice_nodes.transport import RunIdentity

    lock = threading.Lock()
    lock.acquire()
    handle = SimpleNamespace(is_cancel_requested=False)
    terminal = []
    handle.canceled = lambda: terminal.append("cancelled")
    handle.abort = lambda: terminal.append("fault")
    handle.succeed = lambda: terminal.append("success")
    handle.request = RunSpeech.Goal(
        schema_version="run-speech/v1",
        identity=wire.run_identity_to_msg(RunIdentity("run", "epoch", "generation")),
        source=RunSpeech.Goal.FIXTURE,
        fixture_name="fixture.jsonl",
        selected_profile="baseline",
        seed=1,
        hardware=False,
        sad_hold_ms=0,
        config_sha256="a" * 64,
        calibration_sha256="b" * 64,
        clock_domain_fingerprint="input-proof",
        requester_incarnation="client",
    )
    (tmp_path / "fixture.jsonl").write_text(
        json.dumps(
            {
                "generation_id": "generation",
                "clause_id": "one",
                "sequence": 0,
                "text": "Hello.",
                "vector": [0, 0, 0],
                "intensity": 0,
                "seed": 1,
                "end_of_response": True,
            }
        )
        + "\n"
    )
    node = SimpleNamespace(
        incarnation="session-test",
        _admission=lock,
        error=None,
        paths=SimpleNamespace(fixtures=tmp_path),
        committed=0,
        playback_value=SimpleNamespace(drained=True),
        header=lambda _: None,
        recorder_artifact="a" * 64,
    )

    def fail(reason):
        node.error = reason

    node.fail = fail

    def rpc(name, request, **kwargs):
        preparing = request.operation == BeginRun.Request.PREPARE
        if (stage == "prepare" and preparing) or (stage == "start" and not preparing):
            handle.is_cancel_requested = True
        if stage == "fault" and name == "tts":
            raise RuntimeError("admitted worker fault")
        return BeginRun.Response(
            identity=request.identity,
            accepted=True,
            responder_node_name=name,
            responder_incarnation=node.incarnation,
            clock_verified=True,
            lifecycle_state=BeginRun.Response.PREPARED
            if preparing
            else BeginRun.Response.ACTIVE,
        )

    node.rpc = rpc
    node.feedback = lambda _: None

    def publish(_):
        if stage == "playback":
            node.playback_value.drained = False
            handle.is_cancel_requested = True

    node.publisher = SimpleNamespace(publish=publish)

    def finish(name, outcome, reason, goal):
        if stage == "finalize" and goal is not None:
            handle.is_cancel_requested = True
            raise InterruptedError("action cancelled during finalization")

    node.finish_peer = finish
    # Encoding is separately tested; retain real session execution/terminal validator.
    from unittest.mock import patch

    with patch.object(wire, "speech_clause_to_msg", lambda clause, header: clause):
        result = SessionNode.execute(node, handle)
    parsed = wire.validate_run_speech_result(
        result,
        expected_identity=wire.run_identity_from_msg(result.identity),
        expected_responder_incarnation=node.incarnation,
    )
    assert parsed.accepted
    assert parsed.terminal_outcome == ("fault" if stage == "fault" else "cancelled")
    assert terminal == [parsed.terminal_outcome]
    assert node.error and not lock.locked()


@pytest.mark.parametrize(
    "mode", ["missing", "healthy", "cancel-prebuffer", "cancel-start"]
)
def test_first_dac_deadline_after_prebuffer_and_device_start(mode, monkeypatch):
    from alice_nodes.audio import AudioNode

    from alice.speech.pcm_stream import PcmRingBuffer, StreamingAudioPlayback

    started = threading.Event()
    streams = []

    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.active = False
            streams.append(self)

        def start(self):
            self.active = True
            self.origin = time.monotonic()
            started.set()
            if mode == "cancel-start":
                node.cancel.set()
            if mode == "healthy":
                self.callback(
                    np.empty((480, 1), np.float32),
                    480,
                    SimpleNamespace(outputBufferDacTime=0),
                    None,
                )

        @property
        def time(self):
            return time.monotonic() - self.origin

        def abort(self):
            self.active = False

        def close(self):
            pass

    monkeypatch.setitem(
        sys.modules, "sounddevice", SimpleNamespace(OutputStream=Stream)
    )
    ring = PcmRingBuffer(48000)
    if mode != "cancel-prebuffer":
        asyncio.run(ring.put(np.zeros(4800, np.float32)))
    errors = []
    node = SimpleNamespace(
        ring=ring,
        player=StreamingAudioPlayback(ring, 24000),
        cancel=threading.Event(),
        binding=SimpleNamespace(hardware=False),
        get_parameter=lambda _: SimpleNamespace(value=True),
        playing=False,
        last_dac_ns=None,
        first_dac_deadline_ns=None,
        played=0,
        timeline=SimpleNamespace(generated_samples=0),
        transport_samples=4800,
        last_status=None,
        last_credit=-1,
        credit_pub=SimpleNamespace(publish=lambda _: None),
        header=lambda _: None,
        publish_status=lambda: None,
        fail=errors.append,
    )
    node._device_play = MethodType(AudioNode._device_play, node)
    node._emit_clock = MethodType(AudioNode._emit_clock, node)
    # Credit encoding is irrelevant to this clock/stream boundary.
    from alice_nodes import contracts as wire

    monkeypatch.setattr(wire, "stream_header_to_msg", lambda _: None)
    node.last_credit = 480
    # counts is a production property on the real node; expose its value in this double.
    node.counts = SimpleNamespace(consumed_transport=480)
    thread = threading.Thread(target=AudioNode._play, args=(node,))
    thread.start()
    try:
        if mode == "cancel-prebuffer":
            time.sleep(0.02)
            assert node.first_dac_deadline_ns is None
            node.cancel.set()
        else:
            assert started.wait(1)
            if mode == "missing":
                assert node.player.submitted_samples == 0 and node.last_dac_ns is None
                assert node.first_dac_deadline_ns is not None
                AudioNode.check_progress(node, node.first_dac_deadline_ns)
                with pytest.raises(RuntimeError, match="first DAC progress"):
                    AudioNode.check_progress(node, node.first_dac_deadline_ns + 1)
                assert node.last_dac_ns is None
            elif mode == "healthy":
                deadline = time.monotonic() + 1
                while node.last_dac_ns is None and time.monotonic() < deadline:
                    time.sleep(0.002)
                assert node.last_dac_ns is not None
                node.cancel.set()
                thread.join(1)
                AudioNode.check_progress(node, node.last_dac_ns + 250_000_000)
                with pytest.raises(RuntimeError, match="DAC progress expired"):
                    AudioNode.check_progress(node, node.last_dac_ns + 250_000_001)
    finally:
        node.cancel.set()
        thread.join(1)
    assert not thread.is_alive() and not errors
    assert all(not s.active for s in streams)


@pytest.mark.parametrize(
    "damage",
    [
        "valid",
        "mismatch",
        "missing",
        "escape",
        "warm-cache-change",
        "dead-cache-change",
    ],
)
def test_production_pocket_prepare_verifies_assets_before_worker(
    damage, tmp_path, monkeypatch
):
    import alice_nodes.tts as tts
    from alice_nodes.transport import RunIdentity

    root = tmp_path / "cache"
    root.mkdir()
    expected = {}
    for kind in ("model", "tokenizer", "voice"):
        data = kind.encode()
        (root / kind).write_bytes(data)
        expected[kind] = hashlib.sha256(data).hexdigest()
    # The production verifier accepts an explicit manifest for controlled fixtures.
    from alice_nodes import model_assets

    monkeypatch.setattr(
        tts,
        "verify_model_assets",
        lambda: model_assets.verify_model_assets(root, expected),
        raising=False,
    )
    called = []

    class Worker:
        is_alive = True
        identity = {"backend": "fixture"}

        def __init__(self, **kwargs):
            called.append("construct")

        async def close(self):
            called.append("close")

        async def stream(self, clause):
            called.append("warm")
            if False:
                yield

    monkeypatch.setattr(tts, "PocketTtsWorker", Worker)
    node = SimpleNamespace(
        identity=RunIdentity("run", "epoch", "generation"),
        binding=SimpleNamespace(seed=1),
        incarnation="tts-test",
        engine=None,
        local_dir=tmp_path,
        get_parameter=lambda _: SimpleNamespace(value="pocket"),
        run_inference=asyncio.run,
    )
    node.run_inference = lambda operation: asyncio.run(operation())
    if damage == "mismatch":
        (root / "model").write_text("changed")
    elif damage == "missing":
        (root / "tokenizer").unlink()
    elif damage == "escape":
        (tmp_path / "outside").write_bytes(b"voice")
        (root / "voice").unlink()
        (root / "voice").symlink_to(tmp_path / "outside")
    if damage in {"mismatch", "missing", "escape"}:
        with pytest.raises((ValueError, OSError)):
            tts.TtsNode.prepare_run(node)
        assert not called and not (tmp_path / "model.json").exists()
    else:
        tts.TtsNode.prepare_run(node)
        evidence = json.loads((tmp_path / "model.json").read_text())
        assets = evidence["loaded_assets"]["assets"]
        assert {a["repository_relative_path"]: a["sha256"] for a in assets} == expected
        if damage == "dead-cache-change":
            node.engine.is_alive = False
            (root / "model").write_text("later cache bytes")
            with pytest.raises(ValueError, match="checksum mismatch"):
                tts.TtsNode.prepare_run(node)
            assert node.engine is None and called.count("construct") == 1
        if damage == "warm-cache-change":
            (root / "model").write_text("later cache bytes")
            tts.TtsNode.prepare_run(node)
            assert json.loads((tmp_path / "model.json").read_text()) == evidence
            assert called.count("construct") == 1


def test_ros_worker_cannot_silently_reload_after_loaded_process_dies(monkeypatch):
    from alice_nodes.tts import PocketTtsWorker

    from alice.speech.tts_worker import PocketTtsWorker as BaseWorker

    starts = []
    monkeypatch.setattr(BaseWorker, "_start", lambda self: starts.append(True))
    worker = PocketTtsWorker(offline=True)
    worker._start()
    assert starts == [True]
    with pytest.raises(RuntimeError, match="verified worker exited"):
        worker._start()
    assert starts == [True]


@pytest.mark.parametrize("damage", ["missing", "mismatch", "escape"])
def test_asset_rejection_through_actual_begin_run(damage, tmp_path, monkeypatch):
    from pathlib import Path

    import rclpy
    from alice_nodes import model_assets, tts
    from alice_nodes.base import RuntimePaths
    from rclpy.parameter import Parameter
    from test_lifecycle import prepare

    cache = tmp_path / "cache"
    cache.mkdir()
    expected = {"model": hashlib.sha256(b"model").hexdigest()}
    if damage == "mismatch":
        (cache / "model").write_bytes(b"wrong")
    elif damage == "escape":
        (tmp_path / "outside").write_bytes(b"model")
        (cache / "model").symlink_to(tmp_path / "outside")
    monkeypatch.setattr(
        tts,
        "verify_model_assets",
        lambda: model_assets.verify_model_assets(cache, expected),
    )

    def forbidden(**kwargs):
        pytest.fail("unverified assets reached worker construction")

    monkeypatch.setattr(tts, "PocketTtsWorker", forbidden)
    rclpy.init()
    node = tts.create_node(
        paths=RuntimePaths(
            Path("/workspace/config"),
            Path("/workspace/hardware"),
            tmp_path / "runs",
            Path("/workspace/config/speech"),
        )
    )
    node.set_parameters([Parameter("tts_mode", value="pocket")])
    try:
        reply = node.begin(prepare(node))
        assert not reply.accepted and reply.error
        assert node.engine is None
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_invalid_goal_rejected_before_action_admission():
    from alice_interfaces.action import RunSpeech
    from alice_nodes.session import SessionNode
    from rclpy.action import GoalResponse

    node = SimpleNamespace(_admission=threading.Lock())
    assert SessionNode.goal(node, RunSpeech.Goal()) == GoalResponse.REJECT
    assert not node._admission.locked()
