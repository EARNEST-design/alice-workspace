"""Hash the exact reviewed image inputs, independent of clean/dirty Git state."""

import hashlib
import json
from pathlib import Path

root = Path("/opt/alice")
files = {}
for name in ("src", "config", "hardware", "ros/src", "deployment"):
    for path in sorted((root / name).rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            files[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
(root / "source-manifest.json").write_text(
    json.dumps({"sha256": digest, "files": files}, indent=2) + "\n"
)
