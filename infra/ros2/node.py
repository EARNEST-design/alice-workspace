#!/opt/alice/venv/bin/python
"""Execute the installed entrypoint with immutable build and deployment identity."""

import json
import os
import sys
from pathlib import Path

role, *extra = sys.argv[1:]
if role not in {
    "session",
    "tts",
    "audio",
    "expression",
    "motion",
    "maestro",
    "perception",
    "recorder",
}:
    raise SystemExit("unknown participant")
source = json.loads(Path("/opt/alice/source-manifest.json").read_text())
command = [
    f"/opt/alice/ros/install/alice_nodes/lib/alice_nodes/{role}",
    "--ros-args",
    "-p",
    "config_root:=/opt/alice/config",
    "-p",
    "hardware_root:=/opt/alice/hardware",
    "-p",
    "fixtures_root:=/fixtures",
    "-p",
    "output_root:=/artifacts",
    "-p",
    "image_identity:=" + os.environ.get("ALICE_IMAGE_ID", "unprovided"),
    "-p",
    "code_identity:=sha256:" + source["sha256"],
    *extra,
]
os.execv(command[0], command)
