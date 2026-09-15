"""No-device qualification: hidden regular-file owners cannot authorize ROS hardware."""

import argparse
import ctypes
import json
import os
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["owner", "client"])
    options = parser.parse_args()
    root = Path("/tmp/owner-probe")
    if options.mode == "owner":
        root.mkdir(mode=0o777)
        root.chmod(0o777)
        files = [(root / f"if{interface}").open("w+") for interface in ("00", "02")]
        for file in files:
            Path(file.name).chmod(0o666)
        assert ctypes.CDLL(None).prctl(4, 0, 0, 0, 0) == 0  # PR_SET_DUMPABLE
        (root / "ready.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "open_interfaces": ["00", "02"],
                    "regular_files_only": True,
                }
            )
        )
        while True:
            time.sleep(1)
    else:
        import rclpy
        from alice_interfaces.srv import BeginRun
        from alice_nodes.base import (
            RuntimeNode,
            RuntimePaths,
            clock_proof,
            config_digest,
        )
        from alice_nodes.contracts import run_identity_to_msg
        from alice_nodes.transport import RunIdentity
        from rclpy.parameter import Parameter

        owner = json.loads((root / "ready.json").read_text())
        assert os.getuid() == 1000
        try:
            list(Path(f"/proc/{owner['pid']}/fd").iterdir())
            inaccessible = False
        except PermissionError:
            inaccessible = True
        assert inaccessible, (
            "the owner must actually be hidden from checker credentials"
        )
        result = subprocess.run(
            ["fuser", str(root / "if00"), str(root / "if02")],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert not result.stdout.strip(), "hidden owner unexpectedly reported"
        rclpy.init()
        node = RuntimeNode(
            "maestro",
            paths=RuntimePaths(
                Path("/opt/alice/config"),
                Path("/opt/alice/hardware"),
                root / "evidence",
                Path("/fixtures"),
            ),
        )
        calls = []
        node.set_parameters([Parameter("hardware_enabled", value=True)])
        node.prepare_run = lambda: calls.append("prepare")
        node.start_run = lambda: calls.append("factory")
        try:
            identity = RunIdentity("hidden-owner-probe", "hidden-owner-epoch", "test")
            request = BeginRun.Request(
                schema_version="begin-run/v1",
                identity=run_identity_to_msg(identity),
                operation=BeginRun.Request.PREPARE,
                selected_profile="visible-face",
                seed=29,
                hardware=True,
                sad_hold_ms=0,
                config_sha256=config_digest(node.paths.config, "visible-face"),
                calibration_sha256=node.calibration,
                clock_domain_fingerprint=clock_proof(identity.epoch),
                requester_incarnation="probe-session",
                peers=[],
            )
            reply = node.begin(request)
            evidence = {
                "regular_files_only": True,
                "hidden_owner_holds_both_interfaces": owner["open_interfaces"]
                == ["00", "02"],
                "owner_fd_table_inaccessible": inaccessible,
                "fuser_returncode": result.returncode,
                "fuser_stdout": result.stdout,
                "fuser_stderr": result.stderr,
                "hardware_enabled": True,
                "accepted": reply.accepted,
                "error": reply.error,
                "identity_mutated": node.identity is not None,
                "calls": calls,
            }
            print(json.dumps(evidence), flush=True)
            assert not reply.accepted and "host FD visibility" in reply.error
            assert node.identity is None and not calls
        finally:
            node.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    main()
