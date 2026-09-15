#!/usr/bin/env python3
"""Build/start an isolated idle Compose graph with user-owned evidence storage."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra/ros2"


def run(command, **kwargs):
    return subprocess.run(command, check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["build", "up", "down", "config", "run", "preview"]
    )
    parser.add_argument("--project", default="alice-runtime")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/ros2/local")
    parser.add_argument(
        "--overlay",
        action="append",
        choices=["offline", "audio", "hardware"],
        default=[],
    )
    options, remainder = parser.parse_known_args()
    options.args = remainder[1:] if remainder[:1] == ["--"] else remainder
    if options.args and options.command != "run":
        parser.error("extra arguments are only accepted by run")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", options.project):
        parser.error("project must be a lowercase Compose identifier")
    if options.command == "build":
        for stage in ("core", "speech", "perception", "test"):
            run(
                [
                    "docker",
                    "build",
                    "--network",
                    "host",
                    "--target",
                    stage,
                    "-t",
                    f"alice-ros2:{stage}",
                    "-f",
                    str(INFRA / "Dockerfile"),
                    str(ROOT),
                ]
            )
        return
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not os.access(output, os.W_OK):
        parser.error("output must be writable by the invoking user")
    env = dict(
        os.environ,
        ALICE_UID=str(os.getuid()),
        ALICE_GID=str(os.getgid()),
        ALICE_ARTIFACTS=str(output),
    )
    images = {}
    for role in ("core", "speech", "perception", "test"):
        tag = env.get(f"ALICE_{role.upper()}_IMAGE", f"alice-ros2:{role}")
        identity = run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
            capture_output=True,
        ).stdout.strip()
        # Use immutable IDs for both execution and recorded provenance.
        env[f"ALICE_{role.upper()}_IMAGE"] = identity
        env[f"ALICE_{role.upper()}_ID"] = identity
        images[role] = identity
    compose = [
        "docker",
        "compose",
        "--project-name",
        options.project,
        "-f",
        str(INFRA / "compose.yaml"),
    ]
    for overlay in options.overlay:
        compose += ["-f", str(INFRA / f"compose.{overlay}.yaml")]
    if options.command in {"up", "preview"}:
        import time

        record = {
            "images": images,
            "overlays": options.overlay,
            "uid": os.getuid(),
            "gid": os.getgid(),
            "project": options.project,
            "modes": {
                "tts": "pocket"
                if "offline" in options.overlay or "hardware" in options.overlay
                else "synthetic",
                "audio": "speaker" if "audio" in options.overlay else "simulated",
                "maestro": "explicit-hardware-action-required"
                if "hardware" in options.overlay
                else "simulated",
                "perception": "c525-on-hardware-action"
                if "hardware" in options.overlay
                else "replay",
            },
        }
        (output / f"deployment-{time.time_ns()}.json").write_text(
            json.dumps(record, indent=2) + "\n"
        )
        if options.command == "preview":
            compose += ["--profile", "preview"]
        compose += ["up", "-d", "--wait", "--wait-timeout", "90"]
    elif options.command == "run":
        compose += [
            "--profile",
            "tools",
            "run",
            "--rm",
            "tools",
            *(
                options.args
                or [
                    "ros2",
                    "run",
                    "alice_nodes",
                    "alice",
                    "run",
                    "--fixture",
                    "stream-visible-demo-v1.jsonl",
                ]
            ),
        ]
    elif options.command == "down":
        compose += ["--profile", "*", "down"]
    else:
        compose += [options.command]
    run(compose, env=env)


if __name__ == "__main__":
    main()
