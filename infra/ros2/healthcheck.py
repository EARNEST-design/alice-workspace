"""Read-only local process diagnostic; graph/run checks remain separate."""

from pathlib import Path

commands = []
for path in Path("/proc").glob("[0-9]*/cmdline"):
    try:
        commands.append(path.read_bytes())
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        continue
raise SystemExit(0 if any(b"/lib/alice_nodes/" in value for value in commands) else 1)
