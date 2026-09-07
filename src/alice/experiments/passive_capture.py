"""Compatibility exports for passive capture and analysis entry points."""

from alice.experiments.capture_runner import BlendshapeObserver, run_passive_capture
from alice.experiments.config import PassiveCaptureConfig
from alice.experiments.passive_cli import analyze_passive_run, build_parser, main

__all__ = [
    "BlendshapeObserver",
    "PassiveCaptureConfig",
    "analyze_passive_run",
    "build_parser",
    "main",
    "run_passive_capture",
]


if __name__ == "__main__":
    raise SystemExit(main())
