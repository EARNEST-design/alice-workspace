"""Cross-container lifecycle service probes with seven other participants idle."""

import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

import rclpy
from alice_interfaces.msg import PeerIdentity
from alice_interfaces.srv import BeginRun, EndRun
from alice_nodes import contracts as wire
from alice_nodes.base import clock_proof, config_digest
from alice_nodes.transport import RunIdentity
from rclpy.node import Node
from std_srvs.srv import Trigger

from alice.hardware.face_scope import face_manifest
from alice.hardware.manifest import load_manifest


def main():
    scenario = sys.argv[1]
    role = "maestro" if scenario == "queued-start" else "recorder"
    output = Path("/artifacts")
    rclpy.init()
    node = Node("lifecycle_boundary_probe")
    begin = node.create_client(BeginRun, f"/alice/{role}/begin_run")
    end = node.create_client(EndRun, f"/alice/{role}/end_run")
    release = node.create_client(Trigger, f"/qualification/{role}/release")
    records = []

    def wait(future, seconds=8):
        deadline = time.monotonic() + seconds
        while not future.done() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.01)
        assert future.done(), "bounded service deadline"
        return future.result()

    def until(predicate, seconds=3):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.01)
        assert predicate(), "bounded evidence deadline"

    def prepare(epoch):
        return BeginRun.Request(
            schema_version="begin-run/v1",
            identity=wire.run_identity_to_msg(
                RunIdentity("lifecycle", epoch, "generation")
            ),
            operation=BeginRun.Request.PREPARE,
            selected_profile="visible-face",
            seed=29,
            hardware=False,
            sad_hold_ms=0,
            config_sha256=config_digest(Path("/opt/alice/config"), "visible-face"),
            calibration_sha256=face_manifest(
                load_manifest(Path("/opt/alice/hardware/alice-face-v1.yaml"))
            ).calibration_sha256,
            clock_domain_fingerprint=clock_proof(epoch),
            requester_incarnation="probe-session",
        )

    def terminate(request, outcome):
        return wait(
            end.call_async(
                EndRun.Request(
                    schema_version="end-run/v1",
                    identity=request.identity,
                    outcome=outcome,
                    reason="qualification",
                    requester_incarnation="probe-session",
                )
            )
        )

    try:
        assert begin.wait_for_service(timeout_sec=15)
        assert release.wait_for_service(timeout_sec=15)
        epoch = uuid.uuid4().hex
        request = prepare(epoch)
        reply = wait(begin.call_async(request))
        assert reply.accepted, reply.error
        until(lambda: (output / "lifecycle-entered.json").exists())
        terminal = (
            output
            / hashlib.sha256(epoch.encode()).hexdigest()[:24]
            / role
            / "terminal.json"
        )
        if scenario == "queued-start":
            request.operation = BeginRun.Request.START
            request.peers = [
                PeerIdentity(
                    node_name=name,
                    incarnation=reply.responder_incarnation
                    if name == role
                    else "probe-" + name,
                )
                for name in (
                    "session tts audio expression motion maestro perception recorder"
                ).split()
            ]
            pending = begin.call_async(request)
            for _ in range(10):
                rclpy.spin_once(node, timeout_sec=0.01)
            assert not pending.done()
            assert terminate(request, EndRun.Request.CANCELLED).accepted
        else:
            response = terminate(request, EndRun.Request.SUCCESS)
            assert response.accepted and not response.completed
            time.sleep(0.1)
            assert not terminal.exists(), "success cleanup overtook admitted work"
        if scenario != "success-retirement-error":
            until(terminal.exists)
            assert not (output / "lifecycle-retired.json").exists()
            replacement = wait(begin.call_async(prepare(uuid.uuid4().hex)))
            assert not replacement.accepted, "old work allowed a new epoch"
        assert wait(release.call_async(Trigger.Request())).success
        until(terminal.exists)
        until(lambda: (output / "lifecycle-retired.json").exists())
        evidence = json.loads(terminal.read_text())
        assert evidence["outcome"] != "success" and evidence["error"]
        if scenario == "queued-start":
            response = wait(pending)
            assert not response.accepted
            assert not (output / "factory-called.json").exists()
        elif scenario == "success-retirement-error":
            assert "late admitted operation failure" in evidence["error"]
        else:
            assert "retire" in evidence["error"] and "deadline" in evidence["error"]
        digest = hashlib.sha256(terminal.read_bytes()).hexdigest()
        for _ in range(2):
            response = terminate(request, EndRun.Request.SUCCESS)
            assert (
                not response.accepted
                and "fault cannot become success" in response.error
            )
            records.append(
                {"late_success_accepted": response.accepted, "error": response.error}
            )
        assert hashlib.sha256(terminal.read_bytes()).hexdigest() == digest
        # Retired work now permits another epoch. No START is sent for it.
        replacement = prepare(uuid.uuid4().hex)
        response = wait(begin.call_async(replacement))
        assert response.accepted, response.error
        terminate(replacement, EndRun.Request.CANCELLED)
        wait(release.call_async(Trigger.Request()))
        (output / "lifecycle-probe.json").write_text(
            json.dumps(
                {
                    "scenario": scenario,
                    "passed": True,
                    "records": records,
                    "new_epoch_after_retirement": True,
                    "prior_terminal_sha256": digest,
                },
                indent=2,
            )
        )
        print("cross-container lifecycle probe passed: " + scenario)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
