"""Runtime admission and failure semantics using generated Lyrical requests."""

import importlib
import time
from pathlib import Path

import pytest
from alice_interfaces.msg import PeerIdentity
from alice_interfaces.srv import BeginRun, EndRun
from alice_nodes.contracts import run_identity_to_msg
from alice_nodes.transport import RUNTIME_NODE_NAMES, RunIdentity


def runtime_module(name):
    assert importlib.util.find_spec(f"alice_nodes.{name}") is not None, (
        f"missing runtime {name}"
    )
    return importlib.import_module(f"alice_nodes.{name}")


@pytest.fixture
def node(tmp_path):
    import rclpy

    base = runtime_module("base")
    rclpy.init()
    instance = base.RuntimeNode(
        "motion",
        paths=base.RuntimePaths(
            Path("/workspace/config"),
            Path("/workspace/hardware"),
            tmp_path,
            Path("/workspace/config/speech"),
        ),
    )
    yield instance
    instance.destroy_node()
    rclpy.shutdown()


def prepare(node, epoch="epoch-one"):
    from alice_nodes.base import clock_proof, config_digest

    identity = RunIdentity("run", epoch, "generation")
    return BeginRun.Request(
        schema_version="begin-run/v1",
        identity=run_identity_to_msg(identity),
        operation=BeginRun.Request.PREPARE,
        selected_profile="visible-face",
        seed=29,
        hardware=False,
        sad_hold_ms=0,
        config_sha256=config_digest(node.paths.config, "visible-face"),
        calibration_sha256=node.calibration,
        clock_domain_fingerprint=clock_proof(epoch),
        requester_incarnation="session-incarnation",
        peers=[],
    )


def start(node, request):
    request.operation = BeginRun.Request.START
    request.peers = [
        PeerIdentity(
            node_name=n,
            incarnation=node.incarnation
            if n == node.role
            else "session-incarnation"
            if n == "session"
            else f"{n}-incarnation",
        )
        for n in sorted(RUNTIME_NODE_NAMES)
    ]
    return node.begin(request)


def test_start_requires_prepare(node):
    request = prepare(node)
    assert not start(node, request).accepted


def test_one_active_run_and_exact_prepare_retry(node):
    request = prepare(node)
    assert node.begin(request).accepted
    assert node.begin(request).idempotent
    assert not node.begin(prepare(node, "other-epoch")).accepted
    assert start(node, request).accepted
    assert node.begin(request).idempotent


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "unknown"),
        ("config_sha256", "a" * 64),
        ("calibration_sha256", "b" * 64),
        ("clock_domain_fingerprint", "wrong"),
    ],
)
def test_prepare_rejects_changed_schema_configuration_and_clock(node, field, value):
    request = prepare(node)
    setattr(request, field, value)
    assert not node.begin(request).accepted
    assert node.identity is None


def test_start_roster_must_include_this_incarnation(node):
    request = prepare(node)
    assert node.begin(request).accepted
    start(node, request)
    request.peers[0].incarnation = "replacement"
    assert not node.begin(request).accepted


def test_fault_latches_and_end_cannot_turn_fault_into_success(node):
    request = prepare(node)
    node.begin(request)
    start(node, request)
    node.fail("lost peer")
    reply = node.end(
        EndRun.Request(
            schema_version="end-run/v1",
            identity=request.identity,
            outcome=0,
            reason="done",
            requester_incarnation="session-incarnation",
        )
    )
    assert not reply.accepted
    assert node.error == "lost peer"
    assert not node.begin(request).accepted


def test_restarted_first_publisher_is_rejected(node):
    from alice_nodes.transport import StreamHeader

    request = prepare(node)
    node.begin(request)
    start(node, request)
    header = StreamHeader(node.identity, 0, time.monotonic_ns(), "restarted-expression")
    with pytest.raises(ValueError, match="incarnation"):
        node.admit_header(header, "expression", "expression")


def test_expired_original_source_is_never_refreshed(node):
    from alice_nodes.transport import StreamHeader

    request = prepare(node)
    node.begin(request)
    start(node, request)
    header = StreamHeader(
        node.identity, 0, time.monotonic_ns() - 251_000_000, "expression-incarnation"
    )
    with pytest.raises(ValueError, match="expired"):
        node.admit_header(header, "expression", "expression")


def test_stale_epoch_dropped_before_invalid_body(node):
    from alice_interfaces.msg import ExpressionFrame

    node.begin(prepare(node))
    message = ExpressionFrame()
    assert not node.current(message)
    assert node.error is None


def test_sibling_fault_propagates_and_simultaneous_health_is_per_peer(node):
    from alice_interfaces.msg import RunHealth
    from alice_nodes.contracts import stream_header_to_msg
    from alice_nodes.transport import StreamHeader

    request = prepare(node)
    node.begin(request)
    start(node, request)
    for n in sorted(RUNTIME_NODE_NAMES - {node.role}):
        header = StreamHeader(
            node.identity,
            0,
            time.monotonic_ns(),
            "session-incarnation" if n == "session" else f"{n}-incarnation",
        )
        node.receive_health(
            RunHealth(
                header=stream_header_to_msg(header),
                schema_version="run-health/v1",
                state=RunHealth.ACTIVE,
                progress=1,
            )
        )
    assert len(node.peer_health) == 7
    header = StreamHeader(node.identity, 1, time.monotonic_ns(), "audio-incarnation")
    node.receive_health(
        RunHealth(
            header=stream_header_to_msg(header),
            schema_version="run-health/v1",
            state=RunHealth.FAULT,
            progress=1,
            detail="underflow",
        )
    )
    assert "underflow" in node.error


def test_local_watchdog_faults_when_peer_callbacks_stop(node):
    request = prepare(node)
    node.begin(request)
    start(node, request)
    deadline = time.monotonic() + 1
    while node.error is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "health lease expired" in node.error
    assert node.cancel.is_set()


def test_blocked_prepare_does_not_block_health(node):
    import threading

    import rclpy
    from alice_interfaces.msg import RunHealth
    from alice_nodes.base import HEALTH
    from rclpy.executors import MultiThreadedExecutor

    entered, release = threading.Event(), threading.Event()

    def blocked_prepare():
        entered.set()
        assert release.wait(2)

    node.prepare_run = blocked_prepare
    request = prepare(node)
    responses = []
    operation = threading.Thread(target=lambda: responses.append(node.begin(request)))
    observer = rclpy.create_node("health_observer")
    received = []
    observer.create_subscription(
        RunHealth, "/alice/run/health", lambda message: received.append(message), HEALTH
    )
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    executor.add_node(observer)
    operation.start()
    try:
        assert entered.wait(1)
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.02)
        assert len(received) >= 3
        assert all(message.state == RunHealth.PREPARING for message in received)
        assert operation.is_alive()
    finally:
        release.set()
        operation.join(2)
        executor.shutdown(timeout_sec=1)
        observer.destroy_node()
    assert responses[0].accepted


def test_end_acknowledgement_does_not_claim_async_cleanup_completion(node):
    import threading

    request = prepare(node)
    node.begin(request)
    entered, release = threading.Event(), threading.Event()

    def blocked_finalize(outcome):
        entered.set()
        assert release.wait(2)

    node.finalize_run = blocked_finalize
    end = EndRun.Request(
        schema_version="end-run/v1",
        identity=request.identity,
        outcome=0,
        reason="done",
        requester_incarnation="session-incarnation",
    )
    try:
        reply = node.end(end)
        assert reply.accepted and not reply.completed
        assert entered.wait(1)
        retry = node.end(end)
        assert retry.idempotent and not retry.completed
    finally:
        release.set()
    deadline = time.monotonic() + 1
    while not node._completed and time.monotonic() < deadline:
        time.sleep(0.01)
    final = node.end(end)
    assert final.completed and final.artifact_identity


def test_completed_epoch_cannot_be_readmitted_after_second_run(node):
    request = prepare(node)
    node.begin(request)
    end = EndRun.Request(
        schema_version="end-run/v1",
        identity=request.identity,
        outcome=0,
        reason="done",
        requester_incarnation="session-incarnation",
    )
    node.end(end)
    deadline = time.monotonic() + 1
    while not node._completed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert node.begin(prepare(node, "epoch-two")).accepted
    assert not node.begin(request).accepted


def test_local_fault_finalizes_evidence_without_live_session(node):
    node.begin(prepare(node))
    node.fail("session disappeared")
    deadline = time.monotonic() + 1
    while (
        not (node.local_dir / "terminal.json").exists() and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert (node.local_dir / "terminal.json").exists()
    import json

    assert (
        json.loads((node.local_dir / "terminal.json").read_text())["outcome"] == "fault"
    )


def test_prepare_hook_has_dedicated_worker_ownership(node):
    import threading

    owners = []
    node.prepare_run = lambda: owners.append(threading.current_thread().name)
    assert node.begin(prepare(node)).accepted
    assert owners == ["motion-worker"]


def test_fault_evidence_does_not_wait_for_blocked_inference(node):
    import threading

    entered, release = threading.Event(), threading.Event()
    assert node.begin(prepare(node)).accepted

    def inference():
        entered.set()
        release.wait(3)

    node.submit(inference)
    try:
        assert entered.wait(1)
        node.fail("cancel during inference")
        deadline = time.monotonic() + 0.7
        while (
            not (node.local_dir / "terminal.json").exists()
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert (node.local_dir / "terminal.json").exists()
        assert not node.begin(prepare(node, "next-epoch")).accepted
    finally:
        release.set()


def test_cancelled_queued_start_is_retired_before_next_run(node):
    import threading

    entered, release = threading.Event(), threading.Event()
    calls = []
    request = prepare(node)
    assert node.begin(request).accepted
    node.start_run = lambda: calls.append(node.identity.epoch)

    def blocked():
        entered.set()
        release.wait(3)

    node.submit(blocked)
    assert entered.wait(1)
    replies = []
    starter = threading.Thread(target=lambda: replies.append(start(node, request)))
    starter.start()
    try:
        deadline = time.monotonic() + 1
        while not node._lifecycle_busy and time.monotonic() < deadline:
            time.sleep(0.005)
        node.fail("cancel queued start")
        assert not node.begin(prepare(node, "next-epoch")).accepted
    finally:
        release.set()
        starter.join(2)
    assert not calls
    assert replies and not replies[0].accepted
    deadline = time.monotonic() + 1
    while not node._completed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert node.begin(prepare(node, "next-epoch")).accepted
    assert not calls


def test_timed_out_worker_job_never_runs_after_release(node):
    import threading

    entered, release = threading.Event(), threading.Event()
    assert node.begin(prepare(node)).accepted
    node.submit(lambda: (entered.set(), release.wait(2)))
    assert entered.wait(1)
    calls = []
    try:
        with pytest.raises(RuntimeError, match="deadline"):
            node.call_worker(lambda: calls.append("late"), timeout=0.02)
    finally:
        release.set()
    time.sleep(0.1)
    assert calls == []


def test_blocked_finalizer_keeps_publishing_health(node):
    import threading

    import rclpy
    from alice_interfaces.msg import RunHealth
    from alice_nodes.base import HEALTH
    from rclpy.executors import MultiThreadedExecutor

    assert node.begin(prepare(node)).accepted
    entered, release = threading.Event(), threading.Event()
    node.finalize_run = lambda outcome: (entered.set(), release.wait(2))
    observer = rclpy.create_node("finalizer_health_observer")
    received = []
    observer.create_subscription(
        RunHealth, "/alice/run/health", received.append, HEALTH
    )
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    executor.add_node(observer)
    try:
        node.fail("cancel")
        assert entered.wait(1)
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.02)
        assert len(received) >= 3
        assert all(message.state == RunHealth.FAULT for message in received)
        assert not node._completed
    finally:
        release.set()
        executor.shutdown(timeout_sec=1)
        observer.destroy_node()


def successful_end(node, request):
    return node.end(
        EndRun.Request(
            schema_version="end-run/v1",
            identity=request.identity,
            outcome=EndRun.Request.SUCCESS,
            reason="completed",
            requester_incarnation="session-incarnation",
        )
    )


def test_success_seals_admission_and_waits_for_all_admitted_work(node):
    import threading

    entered, release, cleaned = threading.Event(), threading.Event(), threading.Event()
    observed = []
    request = prepare(node)
    assert node.begin(request).accepted

    def blocked():
        entered.set()
        release.wait(3)
        observed.append("first")

    def cleanup(outcome):
        observed.append((outcome, node._outstanding))
        cleaned.set()

    node.finalize_run = cleanup
    node.submit(blocked)
    assert entered.wait(1)
    node.submit(lambda: observed.append("admitted latest"), latest="pending")
    try:
        reply = successful_end(node, request)
        assert reply.accepted and not reply.completed
        late = node.submit(lambda: observed.append("after seal"), latest="pending")
        assert late.retired, "EndRun did not seal ordinary admission"
        assert not cleaned.wait(0.1), "success cleanup overtook admitted work"
        assert not node._completed and not (node.local_dir / "terminal.json").exists()
    finally:
        release.set()
    assert cleaned.wait(1)
    assert observed == ["first", "admitted latest", ("success", 0)]


def test_pending_success_retains_late_ordinary_error(node):
    import json
    import threading

    entered, release = threading.Event(), threading.Event()
    outcomes = []
    request = prepare(node)
    assert node.begin(request).accepted
    node.finalize_run = outcomes.append

    def blocked():
        entered.set()
        release.wait(3)
        raise RuntimeError("late admitted operation failure")

    node.submit(blocked)
    assert entered.wait(1)
    try:
        assert successful_end(node, request).accepted
        time.sleep(0.05)
        assert not node._completed and outcomes == []
    finally:
        release.set()
    deadline = time.monotonic() + 1
    while not node._completed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert node._completed and "late admitted operation failure" in node.error
    terminal = json.loads((node.local_dir / "terminal.json").read_text())
    assert terminal["outcome"] == "fault" and outcomes == ["fault"]


def test_success_retirement_timeout_faults_with_stuck_job_and_blocks_next_run(node):
    import json
    import threading

    entered, release = threading.Event(), threading.Event()
    outcomes = []
    request = prepare(node)
    assert node.begin(request).accepted
    node.finalize_run = outcomes.append
    node.submit(lambda: (entered.set(), release.wait(4)))
    assert entered.wait(1)
    try:
        assert successful_end(node, request).accepted
        deadline = time.monotonic() + 2.5
        while not node._completed and time.monotonic() < deadline:
            time.sleep(0.01)
        assert node._completed and node.error, "stuck work was finalized successfully"
        assert "retire" in node.error and "deadline" in node.error
        assert outcomes == ["fault"]
        assert (
            json.loads((node.local_dir / "terminal.json").read_text())["outcome"]
            == "fault"
        )
        assert node._outstanding == 1
        assert not node.begin(prepare(node, "next-epoch")).accepted
    finally:
        release.set()


def test_cancellation_interrupts_success_wait_without_waiting_for_stuck_job(node):
    import threading

    entered, release = threading.Event(), threading.Event()
    request = prepare(node)
    assert node.begin(request).accepted
    node.submit(lambda: (entered.set(), release.wait(3)))
    assert entered.wait(1)
    try:
        assert successful_end(node, request).accepted
        node.fail("cancel pending success")
        deadline = time.monotonic() + 0.5
        while not node._completed and time.monotonic() < deadline:
            time.sleep(0.01)
        assert node._completed and node.error == "cancel pending success"
        assert node._outstanding == 1
    finally:
        release.set()


@pytest.mark.parametrize(
    "bad_proof",
    [
        "0" * 64,
        "host-monotonic-zero/v0:" + "0" * 64,
        "host-monotonic-zero/v1:" + "0" * 64,
        "host-monotonic-zero/v2:" + "0" * 64,
    ],
)
def test_clock_proof_incompatibility_rejects_before_prepare_or_factory(node, bad_proof):
    request = prepare(node)
    request.clock_domain_fingerprint = bad_proof
    calls = []
    node.prepare_run = lambda: calls.append("prepare")
    node.start_run = lambda: calls.append("factory")
    reply = node.begin(request)
    assert not reply.accepted
    assert "clock-domain mismatch" in reply.error
    assert node.identity is None
    assert not calls


@pytest.mark.parametrize("children_namespace", ["", "time:[bad]", "time:[1]", None])
def test_offset_binding_rejection_precedes_mutation_and_factories(
    node, monkeypatch, children_namespace
):
    import alice_nodes.clock as clock

    request = prepare(node)
    original = clock.os.readlink

    def readlink(path):
        if path == "/proc/self/ns/time_for_children":
            if children_namespace is None:
                raise FileNotFoundError("missing child namespace")
            return children_namespace
        return original(path)

    calls = []
    node.prepare_run = lambda: calls.append("prepare")
    node.start_run = lambda: calls.append("factory")
    monkeypatch.setattr(clock.os, "readlink", readlink)
    reply = node.begin(request)
    assert not reply.accepted
    assert "namespace" in reply.error
    assert node.identity is None and not calls


def test_ros_hardware_rejects_unqualified_host_visibility_before_mutation(
    node, monkeypatch
):
    from rclpy.parameter import Parameter

    node.set_parameters([Parameter("hardware_enabled", value=True)])
    request = prepare(node)
    request.hardware = True
    calls = []
    node.prepare_run = lambda: calls.append("prepare")
    node.start_run = lambda: calls.append("factory")
    reply = node.begin(request)
    assert not reply.accepted
    assert "host FD visibility" in reply.error
    assert "00/02" in reply.error
    assert node.identity is None and not calls
