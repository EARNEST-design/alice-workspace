"""Executable eight-process smoke, invoked inside a no-network test container."""

import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import rclpy
from rclpy.node import Node

roles = [
    "session",
    "tts",
    "audio",
    "expression",
    "motion",
    "maestro",
    "perception",
    "recorder",
]
output = Path(os.environ["ALICE_SMOKE_OUTPUT"])
output.mkdir(parents=True, exist_ok=True)
source_hash = hashlib.sha256()
for tree in (
    Path("/workspace/src/alice"),
    Path("/workspace/ros2_ws/src/alice_nodes/alice_nodes"),
):
    for source in sorted(tree.rglob("*.py")):
        source_hash.update(str(source.relative_to("/workspace")).encode())
        source_hash.update(source.read_bytes())
children = []
launch_commands = {}
try:
    for role in roles:
        log = (output / f"{role}.log").open("w")
        command = [
            "ros2",
            "run",
            "alice_nodes",
            role,
            "--ros-args",
            "-p",
            "config_root:=/workspace/config",
            "-p",
            "hardware_root:=/workspace/hardware",
            "-p",
            f"output_root:={output}/runs",
            "-p",
            "fixtures_root:=/workspace/config/speech",
            "-p",
            "image_identity:=" + os.environ["ALICE_IMAGE_ID"],
            "-p",
            "code_identity:=sha256:" + source_hash.hexdigest(),
        ]
        launch_commands[role] = command
        children.append(
            subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
        )
    rclpy.init()
    probe = Node("graph_probe")
    deadline = time.monotonic() + 20
    names = set()
    while time.monotonic() < deadline:
        names = {n for n, ns in probe.get_node_names_and_namespaces() if ns == "/alice"}
        if set(roles) <= names:
            break
        rclpy.spin_once(probe, timeout_sec=0.1)
    assert set(roles) <= names, names
    print(json.dumps({"node_names": sorted(names)}), flush=True)
    probe.destroy_node()
    rclpy.shutdown()
    epochs = []
    for repetition in range(2):
        command = [
            "ros2",
            "run",
            "alice_nodes",
            "alice",
            "run",
            "--fixture",
            "stream-visible-demo-v1.jsonl",
            "--fixtures-root",
            "/workspace/config/speech",
            "--config-root",
            "/workspace/config",
            "--hardware-root",
            "/workspace/hardware",
        ]
        result = subprocess.run(command, text=True, capture_output=True, timeout=100)
        (output / f"client-{repetition}.log").write_text(result.stdout + result.stderr)
        print(result.stdout + result.stderr, flush=True)
        assert result.returncode == 0, result
        value = json.loads(result.stdout.strip().splitlines()[-1])
        assert (
            value["outcome"] == "success"
            and value["artifact"]
            and value["played_samples"] > 0
        )
        epochs.append(value["identity"]["epoch"])
    assert len(set(epochs)) == 2
    for path in (output / "runs").glob("*/audio/audio.json"):
        audio = json.loads(path.read_text())
        assert audio["transport"] == 24000 and audio["generated"] == 31200
        assert audio["played"] == audio["submitted"] == audio["generated"]
        assert (
            audio["underflows"] == 0
            and audio["ring_max_depth"] <= 48000
            and audio["drained"]
        )
    for path in (output / "runs").glob("*/maestro/commands.jsonl"):
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        writes = [r for r in rows if "status" in r]
        assert writes and any(r["phase"] == "speech" for r in writes)
        assert all(
            r["request"]["targets"][0]["actuator_name"]
            in {
                "lower_eyelids",
                "upper_eyelids",
                "forehead_frown",
                "mouth_open",
                "left_mouth_corner",
                "right_mouth_corner",
            }
            for r in writes
        )
    assert len(list((output / "runs").glob("*/recorder/manifest.json"))) == 2
    from alice_interfaces.msg import PlaybackStatus

    rclpy.init()
    observer = Node("fault_probe")
    playing = []
    observer.create_subscription(
        PlaybackStatus,
        "/alice/audio/playback_status",
        lambda m: playing.append(m) if m.state == PlaybackStatus.PLAYING else None,
        128,
    )
    fault_client = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    deadline = time.monotonic() + 15
    while not playing and time.monotonic() < deadline:
        rclpy.spin_once(observer, timeout_sec=0.01)
    assert playing, "fault run never began playback"
    os.killpg(children[roles.index("expression")].pid, signal.SIGSTOP)
    time.sleep(0.65)
    os.killpg(children[roles.index("expression")].pid, signal.SIGCONT)
    fault_output, _ = fault_client.communicate(timeout=40)
    (output / "fault-client.log").write_text(fault_output)
    print(fault_output, flush=True)
    assert fault_client.returncode != 0
    fault = json.loads(fault_output.splitlines()[0])
    assert fault["outcome"] == "fault"
    observer.destroy_node()
    rclpy.shutdown()
    fault_runs = []
    for path in (output / "runs").glob("*/maestro/terminal.json"):
        if json.loads(path.read_text())["outcome"] == "fault":
            fault_runs.append(path.parent)
    assert len(fault_runs) == 1
    servo = json.loads((fault_runs[0] / "servo.json").read_text())
    assert not servo["home_confirmed"]
    rows = [
        json.loads(line)
        for line in (fault_runs[0] / "commands.jsonl").read_text().splitlines()
    ]
    assert not any(r.get("phase") in {"home", "post-speech"} for r in rows)
    # Drop only control traffic, with every participant process and heartbeat live.
    from alice_interfaces.msg import RunHealth

    for suppressed in ("/alice/speech/state", "/alice/expression/frame"):
        index = roles.index("expression")
        child = children[index]
        original_command = launch_commands["expression"]
        child.send_signal(signal.SIGINT)
        child.wait(timeout=8)
        replacement_log = (
            output / ("suppressed-" + suppressed.rsplit("/", 1)[-1] + ".log")
        ).open("w")
        replacement = original_command + ["-r", suppressed + ":=/alice/test/withheld"]
        children[index] = subprocess.Popen(
            replacement,
            stdout=replacement_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        time.sleep(1)
        rclpy.init()
        observer = Node("first_control_fault_probe")
        observed_health = []
        playing = []
        observer.create_subscription(
            RunHealth, "/alice/run/health", observed_health.append, 64
        )
        observer.create_subscription(
            PlaybackStatus,
            "/alice/audio/playback_status",
            lambda m: playing.append(m) if m.state == PlaybackStatus.PLAYING else None,
            128,
        )
        client = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        deadline = time.monotonic() + 40
        while client.poll() is None and time.monotonic() < deadline:
            rclpy.spin_once(observer, timeout_sec=0.01)
        result, _ = client.communicate(timeout=5)
        (
            output / ("first-control-" + suppressed.rsplit("/", 1)[-1] + ".log")
        ).write_text(result)
        print(result, flush=True)
        assert client.returncode != 0 and playing
        fault = json.loads(result.splitlines()[0])
        assert (
            fault["outcome"] == "fault" and "control source progress" in fault["error"]
        ), fault
        epoch = fault["identity"]["epoch"]
        run_dir = output / "runs" / hashlib.sha256(epoch.encode()).hexdigest()[:24]
        incarnations = {
            json.loads((run_dir / role / "terminal.json").read_text())["incarnation"]
            for role in roles
        }
        # Every admitted participant reported live after PLAYING.
        live = {
            m.header.publisher_incarnation
            for m in observed_health
            if m.header.identity.epoch == epoch
            and m.header.source_monotonic_ns >= playing[0].header.source_monotonic_ns
            and m.state == RunHealth.ACTIVE
        }
        assert incarnations <= live, (incarnations - live, suppressed)
        (run_dir / "first-control-observer.json").write_text(
            json.dumps(
                {
                    "suppressed_topic": suppressed,
                    "playing_source_ns": playing[0].header.source_monotonic_ns,
                    "live_incarnations_after_playing": sorted(live),
                    "health": [
                        {
                            "incarnation": m.header.publisher_incarnation,
                            "source_ns": m.header.source_monotonic_ns,
                            "state": m.state,
                            "detail": m.detail,
                        }
                        for m in observed_health
                        if m.header.identity.epoch == epoch
                    ],
                },
                indent=2,
            )
        )
        servo = json.loads((run_dir / "maestro/servo.json").read_text())
        assert not servo["home_confirmed"]
        rows = [
            json.loads(line)
            for line in (run_dir / "maestro/commands.jsonl").read_text().splitlines()
        ]
        assert not any(
            row.get("phase") in {"speech", "home", "post-speech"} for row in rows
        )
        observer.destroy_node()
        rclpy.shutdown()
    print(
        "eight-process smoke passed: two bounded runs, distinct epochs, "
        "drain/Home/receipts/manifests; sibling stall and first-control "
        "suppression stopped with no Home",
        flush=True,
    )
finally:
    for child in children:
        if child.poll() is None:
            # ros2 run forwards this once to its child. Group SIGINT would
            # deliver a second interrupt during the child's bounded cleanup.
            child.send_signal(signal.SIGINT)
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()

for role in roles:
    assert "Traceback" not in (output / f"{role}.log").read_text(), role
