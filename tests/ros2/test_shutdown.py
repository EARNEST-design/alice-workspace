"""SIGTERM must preserve a live ROS context until local stop and node cleanup."""

import json
import os
import signal
import subprocess
import sys
import time

import pytest


@pytest.mark.parametrize(
    "stuck, closed_stderr", [(False, False), (True, False), (True, True)]
)
def test_sigterm_stops_before_context_destruction(tmp_path, stuck, closed_stderr):
    script = tmp_path / "child.py"
    script.write_text(
        """
import json
import os
from pathlib import Path
import rclpy
import time
import traceback
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.task import Future
original_del = Future.__del__
def diagnostic_del(self):
    if self._exception is not None and not self._exception_fetched:
        traceback.print_exception(self._exception)
    original_del(self)
Future.__del__ = diagnostic_del
from alice_nodes.base import RuntimeNode, RuntimePaths, spin, clock_proof, config_digest
from alice_nodes.contracts import run_identity_to_msg
from alice_nodes.transport import RunIdentity
from alice_interfaces.srv import BeginRun
root = Path(__file__).parent
class Probe(RuntimeNode):
    def __init__(self):
        super().__init__('motion', paths=RuntimePaths(
            Path('/opt/alice/config'), Path('/opt/alice/hardware'),
            root, Path('/fixtures')))
        reply = self.begin(BeginRun.Request(
            schema_version='begin-run/v1',
            identity=run_identity_to_msg(RunIdentity(
                'shutdown', 'shutdown-epoch', 'generation')),
            operation=BeginRun.Request.PREPARE,
            selected_profile='visible-face', seed=29,
            config_sha256=config_digest(self.paths.config, 'visible-face'),
            calibration_sha256=self.calibration,
            clock_domain_fingerprint=clock_proof('shutdown-epoch'),
            requester_incarnation='probe'))
        assert reply.accepted, reply.error
        started = []
        def active_callback():
            started.append(True)
            if len(started) == 6:
                if CLOSE_STDERR:
                    os.close(2)
                (root / 'ready').write_text('ready')
            time.sleep(STALL_SECONDS)
        group = ReentrantCallbackGroup()
        for _ in range(8):
            self.create_timer(.001, active_callback, callback_group=group)
    def stop_local(self):
        (root / 'local-stop.json').write_text(
            json.dumps({'monotonic': time.monotonic()}))
    def destroy_node(self):
        (root / 'result.json').write_text(json.dumps({'context_valid': rclpy.ok()}))
        return super().destroy_node()
spin(Probe)
""".replace("STALL_SECONDS", "60" if stuck else ".3").replace(
            "CLOSE_STDERR", str(closed_stderr)
        )
    )
    child = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=dict(os.environ, ROS_DOMAIN_ID="198"),
    )
    try:
        deadline = time.monotonic() + 8
        while (
            not (tmp_path / "ready").exists()
            and child.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert (tmp_path / "ready").exists()
        signalled = time.monotonic()
        child.send_signal(signal.SIGTERM)
        output, _ = child.communicate(timeout=10)
        stopped = json.loads((tmp_path / "local-stop.json").read_text())
        assert stopped["monotonic"] - signalled < 0.25
        assert next(tmp_path.glob("*/motion/terminal.json")).exists()
        if stuck:
            assert child.returncode == 1, output
            evidence = json.loads(next(tmp_path.glob("shutdown-*.json")).read_text())
            assert evidence["quiescent"] is False
            assert evidence["deadline_seconds"] == 5
            assert evidence["cancel_requested"] and evidence["local_terminal_completed"]
            assert not (tmp_path / "result.json").exists(), (
                "entities destroyed under live handler"
            )
            return
        assert child.returncode == 0, output
        assert (
            "Traceback" not in output
            and "RCLError" not in output
            and "Destroyable" not in output
        ), output
        assert json.loads((tmp_path / "result.json").read_text())["context_valid"]
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
