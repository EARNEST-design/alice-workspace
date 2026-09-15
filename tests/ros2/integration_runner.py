"""Host-owned actual Compose matrix. Never mounts a Docker socket into a node."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLES = "session tts audio expression motion maestro perception recorder".split()
SCENARIOS = [
    "selfkill-session",
    "selfkill-audio",
    "first-control-motion",
    "tts-stall-cancel",
    "expression-stall-cancel",
    "external-relay-aging",
    "terminal-conflict",
    "stale-epoch",
    "sigterm-all",
    "default",
    "success",
    "cancel",
    "gap",
    "duplicate",
    "stale",
    "underflow",
    "delayed-expression",
    "first-control",
    "backpressure",
    "controller",
    "recorder-failure",
    "config-mismatch",
    "clock-mismatch",
    "cancel-hold",
    "kill-session",
    "kill-audio",
    "kill-expression",
    "kill-maestro",
    "kill-recorder",
    "restart-expression",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--scenario",
        action="append",
        choices=SCENARIOS
        + [
            "offline",
            "offline-profile",
            "speaker",
            "preview",
            "clock-bound",
            "clock-binding",
            "callback-underflow",
            "source-exhaustion",
            "queued-start",
            "success-retirement-error",
            "success-retirement-timeout",
        ],
    )
    options = parser.parse_args()
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, ALICE_UID=str(os.getuid()), ALICE_GID=str(os.getgid()))
    images = {}
    for stage in ("core", "speech", "perception", "test"):
        identity = subprocess.check_output(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                f"alice-ros2:{stage}",
            ],
            text=True,
        ).strip()
        env[f"ALICE_{stage.upper()}_IMAGE"] = identity
        env[f"ALICE_{stage.upper()}_ID"] = identity
        images[stage] = identity
    (output / "images.json").write_text(json.dumps(images, indent=2))
    helpers = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT / "tests/ros2").glob("*.py")
    }
    (output / "qualification-source.json").write_text(json.dumps(helpers, indent=2))
    snapshot = output / "harness"
    snapshot.mkdir()
    for path in (ROOT / "tests/ros2").glob("*.py"):
        shutil.copy2(path, snapshot / path.name)
    summary = []
    for index, scenario in enumerate(options.scenario or SCENARIOS):
        run = output / scenario
        run.mkdir()
        project = f"alice-q-{os.getpid()}-{index}"
        env["ALICE_ARTIFACTS"] = str(run)
        env["ROS_DOMAIN_ID"] = str(80 + index)
        compose = [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(ROOT / "infra/ros2/compose.yaml"),
        ]
        if scenario in {"offline", "offline-profile", "speaker"}:
            compose += ["-f", str(ROOT / "infra/ros2/compose.offline.yaml")]
        if scenario == "speaker":
            compose += ["-f", str(ROOT / "infra/ros2/compose.audio.yaml")]
        services = {"tools": {"volumes": [f"{snapshot}:/qualification:ro"]}}
        role = (
            "tts"
            if scenario in {"gap", "duplicate", "stale", "underflow"}
            else "expression"
            if scenario in {"delayed-expression", "first-control"}
            else "recorder"
            if scenario == "recorder-failure"
            else "maestro"
        )
        # A pass-through Maestro instrumentation wrapper records the original
        # timestamp at the actual admission method; injections remain test-only.
        for selected in (
            set(ROLES)
            if scenario in {"clock-bound", "clock-binding"}
            else {role, "maestro"}
        ):
            services[selected] = {
                "volumes": [f"{snapshot}:/qualification:ro"],
                "command": [
                    "python",
                    "/qualification/participant_faults.py",
                    selected,
                    scenario,
                ],
            }
            services[selected]["healthcheck"] = {"disable": True}
        extra_role = {
            "selfkill-session": "session",
            "selfkill-audio": "audio",
            "first-control-motion": "motion",
            "tts-stall-cancel": "tts",
            "expression-stall-cancel": "expression",
            "external-relay-aging": "session",
        }.get(scenario)
        if extra_role:
            services[extra_role] = {
                "volumes": [f"{snapshot}:/qualification:ro"],
                "command": [
                    "python",
                    "/qualification/participant_faults.py",
                    extra_role,
                    scenario,
                ],
                "healthcheck": {"disable": True},
            }
        if scenario == "first-control-motion":
            services["motion"]["command"] += [
                "--ros-args",
                "-r",
                "/alice/expression/frame:=/alice/test/withheld",
            ]
        if scenario in {
            "queued-start",
            "success-retirement-error",
            "success-retirement-timeout",
        }:
            selected = "maestro" if scenario == "queued-start" else "recorder"
            services[selected] = {
                "volumes": [f"{snapshot}:/qualification:ro"],
                "command": [
                    "python",
                    "/qualification/participant_faults.py",
                    selected,
                    scenario,
                ],
                "healthcheck": {"disable": True},
            }
        if scenario in {"offline-profile", "callback-underflow"}:
            for selected in (
                ("expression", "motion", "audio")
                if scenario == "offline-profile"
                else ("audio",)
            ):
                services[selected] = {
                    "volumes": [f"{snapshot}:/qualification:ro"],
                    "command": [
                        "python",
                        "/qualification/participant_faults.py",
                        selected,
                        scenario,
                    ],
                    "healthcheck": {"disable": True},
                }
                if scenario == "offline-profile":
                    # Same liveness scan/cadence, adjusted only for the test wrapper.
                    probe = (
                        (ROOT / "infra/ros2/healthcheck.py")
                        .read_text()
                        .replace(
                            "/lib/alice_nodes/", "/qualification/participant_faults.py"
                        )
                    )
                    services[selected]["healthcheck"] = {
                        "test": ["CMD", "python", "-c", probe]
                    }
        if scenario == "source-exhaustion":
            fixture_root = run / "fixtures"
            fixture_root.mkdir()
            first = (
                (ROOT / "config/speech/stream-visible-demo-v1.jsonl")
                .read_text()
                .splitlines()[0]
            )
            (fixture_root / "stream-visible-demo-v1.jsonl").write_text(first + "\n")
            for selected in ("session", "tools"):
                services.setdefault(selected, {}).setdefault("volumes", []).append(
                    f"{fixture_root}:/fixtures:ro"
                )
        if scenario == "first-control":
            services["expression"]["command"] += [
                "--ros-args",
                "-r",
                "/alice/speech/state:=/alice/test/withheld",
            ]
        if scenario == "backpressure":
            for selected in ("tts", "audio"):
                services[selected] = {
                    "volumes": [f"{snapshot}:/qualification:ro"],
                    "command": [
                        "python",
                        "/qualification/participant_faults.py",
                        selected,
                        scenario,
                    ],
                    "healthcheck": {"disable": True},
                }
        if scenario in {"default", "preview", "sigterm-all"}:
            services = {"tools": services["tools"]}
        if scenario == "clock-mismatch":
            services["maestro"]["environment"] = {"ALICE_CLOCK_FAULT": "1"}
        override = run / "override.yaml"
        override.write_text(yaml.safe_dump({"services": services}))
        compose += ["-f", str(override)]
        log = (run / "orchestration.log").open("w")

        def execute(args, **kwargs):
            return subprocess.run(
                [*compose, *args],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=100,
                **kwargs,
            )

        try:
            if scenario == "preview":
                execute(["--profile", "preview", "up", "-d", "--wait"])
                execute(
                    [
                        "--profile",
                        "tools",
                        "run",
                        "--rm",
                        "tools",
                        "python",
                        "/qualification/description_smoke.py",
                    ]
                )
                summary.append({"scenario": scenario, "passed": True})
                continue
            execute(["up", "-d", "--wait", "--wait-timeout", "90"])
            ids = subprocess.check_output(
                [*compose, "ps", "-q"], env=env, text=True
            ).splitlines()
            inspect = json.loads(
                subprocess.check_output(["docker", "inspect", *ids], text=True)
            )
            (run / "containers.json").write_text(json.dumps(inspect, indent=2))
            assert len(inspect) == 8
            assert len({item["State"]["Pid"] for item in inspect}) == 8
            salt = uuid.uuid4().hex
            proofs = []
            for item in inspect:
                script = (
                    "import json,os;from pathlib import Path;"
                    "from alice_nodes.clock import clock_proof;"
                    "bound=os.readlink('/proc/self/ns/time')==os.readlink('/proc/self/ns/time_for_children');"
                    "text=Path('/proc/self/timens_offsets').read_text();"
                    "rows=[r.split() for r in text.splitlines()];"
                    "print(json.dumps({'proof':clock_proof(" + repr(salt) + "),"
                    "'current_matches_children':bound,'zero':sorted(rows)==[['boottime','0','0'],['monotonic','0','0']]}))"
                )
                value = json.loads(
                    subprocess.check_output(
                        [
                            "docker",
                            "exec",
                            item["Id"],
                            "/opt/alice/entrypoint.sh",
                            "python",
                            "-c",
                            script,
                        ],
                        text=True,
                    )
                )
                proofs.append(value)
            evidence = {
                "participants": 8,
                "all_zero_offsets": all(v["zero"] for v in proofs),
                "same_versioned_epoch_salted_proof": len({v["proof"] for v in proofs})
                == 1,
                "version": "host-monotonic-zero/v2",
                "probe_scope": "exec child; participant calls recorded separately",
                "all_current_match_children": all(
                    v["current_matches_children"] for v in proofs
                ),
                "raw_ids_or_proofs_retained": False,
            }
            (run / "clock-domain.json").write_text(json.dumps(evidence, indent=2))
            assert (
                evidence["all_zero_offsets"]
                and evidence["same_versioned_epoch_salted_proof"]
            )
            for item in inspect:
                host = item["HostConfig"]
                assert (
                    host["ReadonlyRootfs"]
                    and not host["Privileged"]
                    and not host["Devices"]
                )
                assert host["CapDrop"] == ["ALL"] and host["NetworkMode"] != "host"
                assert item["Config"]["User"] == f"{os.getuid()}:{os.getgid()}"
            network = subprocess.check_output(
                ["docker", "network", "inspect", project + "_runtime"], text=True
            )
            (run / "network.json").write_text(network)
            assert json.loads(network)[0]["Internal"]
            if scenario in {"offline", "offline-profile", "speaker"}:
                subprocess.run(
                    [*compose, "exec", "-T", "tts", "python", "-"],
                    input=(snapshot / "model_assets.py").read_text(),
                    text=True,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            if scenario in {
                "queued-start",
                "success-retirement-error",
                "success-retirement-timeout",
            }:
                execute(
                    [
                        "--profile",
                        "tools",
                        "run",
                        "--rm",
                        "tools",
                        "python",
                        "/qualification/lifecycle_probe.py",
                        scenario,
                    ]
                )
                summary.append({"scenario": scenario, "passed": True})
                continue
            client_log = (run / "client.log").open("w")
            client = subprocess.Popen(
                [
                    *compose,
                    "--profile",
                    "tools",
                    "run",
                    "--rm",
                    "tools",
                    "python",
                    "/qualification/compose_client.py",
                    scenario,
                ],
                env=env,
                stdout=client_log,
                stderr=subprocess.STDOUT,
            )
            if (
                scenario.startswith("kill-")
                or scenario.startswith("restart-")
                or scenario == "sigterm-all"
            ):
                deadline = time.monotonic() + 60
                while (
                    not (run / "playing.json").exists()
                    and time.monotonic() < deadline
                    and client.poll() is None
                ):
                    time.sleep(0.01)
                assert (run / "playing.json").exists(), "no PLAYING before injection"
                target = scenario.split("-", 1)[1]
                stamp = time.monotonic_ns()
                execute(
                    ["kill", "-s", "SIGTERM"]
                    if scenario == "sigterm-all"
                    else ["kill", "-s", "SIGKILL", target]
                )
                (run / "injection.json").write_text(
                    json.dumps(
                        {
                            "host_request_ns": stamp,
                            "completed_ns": time.monotonic_ns(),
                            "target": target,
                        }
                    )
                )
                if scenario.startswith("restart-"):
                    execute(["up", "-d", target])
            assert client.wait(timeout=95) == 0, (run / "client.log").read_text()
            time.sleep(1)
            (run / "containers-after-run.json").write_text(
                subprocess.check_output(["docker", "inspect", *ids], text=True)
            )
            result = json.loads((run / "client.json").read_text())
            success = scenario in {
                "stale-epoch",
                "default",
                "clock-bound",
                "success",
                "offline",
                "offline-profile",
                "speaker",
            }
            assert (result["outcome"] == "success") == success, result
            if scenario in {"clock-bound", "clock-binding"}:
                proof_records = {
                    p.stem.removeprefix("participant-clock-"): json.loads(p.read_text())
                    for p in run.glob("participant-clock-*.json")
                }
                if scenario == "clock-bound":
                    assert set(proof_records) == set(ROLES)
                    assert all(
                        v["accepted"]
                        and v["current_matches_children"]
                        and v["zero_offsets"]
                        for v in proof_records.values()
                    )
                else:
                    assert not proof_records["maestro"]["accepted"]
                    assert not proof_records["maestro"]["current_matches_children"]
                    assert "namespace" in result["error"]
                    assert not list(run.glob("*/maestro/commands.jsonl"))

            terminals = list(run.glob("*/maestro/terminal.json"))
            if terminals:
                folder = terminals[0].parent
                terminal = json.loads(terminals[0].read_text())
                servo = json.loads((folder / "servo.json").read_text())
                rows = [
                    json.loads(line)
                    for line in (folder / "commands.jsonl").read_text().splitlines()
                ]
                if not success:
                    assert (
                        terminal["outcome"] != "success" and not servo["home_confirmed"]
                    )
                    assert not any(row.get("phase") == "home" for row in rows)
                injection_path = run / "injection.json"
                if injection_path.exists():
                    injection = json.loads(injection_path.read_text())
                    writes = [
                        row["status"]["reported_monotonic_ns"]
                        for row in rows
                        if "status" in row
                    ]
                    if writes:
                        (run / "stop-latency.json").write_text(
                            json.dumps(
                                {
                                    "last_mock_write_ns": max(writes),
                                    "injection_request_ns": injection[
                                        "host_request_ns"
                                    ],
                                    "request_to_last_write_ms": max(
                                        0, max(writes) - injection["host_request_ns"]
                                    )
                                    / 1e6,
                                    "measurement": (
                                        "host kill request to last local mock receipt; "
                                        "both clocks unshifted"
                                    ),
                                },
                                indent=2,
                            )
                        )
                timing_path = folder / "qualification-timing.json"
                if timing_path.exists():
                    ages = sorted(
                        (row["admission_ns"] - row["source_ns"]) / 1e6
                        for row in json.loads(timing_path.read_text())
                    )
                    if ages:
                        (run / "timing.json").write_text(
                            json.dumps(
                                {
                                    "count": len(ages),
                                    "p99_ms": ages[
                                        min(len(ages) - 1, int(len(ages) * 0.99))
                                    ],
                                    "max_ms": max(ages),
                                    "target_p99_ms": 20,
                                    "target_met": ages[
                                        min(len(ages) - 1, int(len(ages) * 0.99))
                                    ]
                                    < 20,
                                },
                                indent=2,
                            )
                        )
                        assert max(ages) <= 250
            if scenario == "callback-underflow":
                audio = json.loads(next(run.glob("*/audio/audio.json")).read_text())
                assert audio["underflows"] == 1 and not audio["drained"]
            if scenario == "backpressure":
                credit = json.loads(
                    next(run.glob("*/tts/qualification-credit.json")).read_text()
                )
                assert 0 < credit["max_outstanding"] <= credit["capacity"] == 48000
                assert credit["sent"] > 48000, credit
                duplicates = json.loads(
                    next(
                        run.glob("*/audio/qualification-duplicate-credit.json")
                    ).read_text()
                )
                assert duplicates["count"] > 0
            if scenario in {"first-control", "first-control-motion"}:
                assert "control source progress" in result["error"], result
                observer = json.loads((run / "observer.json").read_text())
                assert len({h["incarnation"] for h in observer["health"]}) == 8
            if success:
                audio_path = next(
                    path
                    for path in run.glob("*/audio/audio.json")
                    if json.loads(path.read_text())["generated"] > 0
                )
                audio = json.loads(audio_path.read_text())
                assert audio["generated"] == audio["submitted"] == audio["played"]
                assert (
                    audio["underflows"] == 0
                    and audio["drained"]
                    and audio["ring_max_depth"] <= 48000
                )
                assert audio["generated"] <= 240000
                assert servo["home_confirmed"]
                assert len(list(run.glob("*/recorder/manifest.json"))) == 1
                events = [
                    json.loads(line)
                    for line in next(run.glob("*/recorder/events.jsonl"))
                    .read_text()
                    .splitlines()
                ]
                faces = [
                    item["message"]
                    for item in events
                    if item["producer"] == "perception"
                ]
                assert faces and all(
                    item["validity"] == 1
                    and not item["scores"]
                    and item["invalid_reason"]
                    for item in faces
                )
                (run / "no-face.json").write_text(
                    json.dumps(
                        {
                            "count": len(faces),
                            "explicit_no_face": True,
                            "scores_inferred": False,
                        }
                    )
                )
            summary.append({"scenario": scenario, "passed": True, "result": result})
        except Exception as exc:
            summary.append({"scenario": scenario, "passed": False, "error": str(exc)})
        finally:
            subprocess.run(
                [*compose, "--profile", "*", "stop", "--timeout", "15"],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            stopped = subprocess.run(
                [*compose, "--profile", "*", "ps", "--all", "--format", "json"],
                env=env,
                capture_output=True,
                text=True,
            )
            (run / "post-stop-containers.jsonl").write_text(stopped.stdout)
            subprocess.run(
                [*compose, "--profile", "*", "logs", "--no-color"],
                env=env,
                stdout=(run / "services.log").open("w"),
                stderr=subprocess.STDOUT,
            )
            subprocess.run(
                [
                    *compose,
                    "--profile",
                    "*",
                    "down",
                    "--timeout",
                    "15",
                    "--remove-orphans",
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            log.close()
            (output / "summary.json").write_text(json.dumps(summary, indent=2))
            print(json.dumps(summary[-1]), flush=True)
    assert all(item["passed"] for item in summary), "Compose matrix failures retained"


if __name__ == "__main__":
    main()
