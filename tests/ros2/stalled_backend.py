"""Pickle-safe backend for an actual owned subprocess blocked before first PCM."""

import json
import time
from pathlib import Path


class StalledBackend:
    identity = {"qualification": "stalled-first-chunk-subprocess"}

    def stream(self, text, *, voice, seed):
        Path("/artifacts/tts-stall-entered.json").write_text(
            json.dumps({"monotonic_ns": time.monotonic_ns()})
        )
        time.sleep(60)
        yield  # The owner must terminate this process before any output.
