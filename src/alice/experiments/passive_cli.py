"""Offline CLI for passive stability and repeatability analysis."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from alice.analysis.manifest import AnalysisManifest
from alice.analysis.publication import (
    publish_repeatability_analysis,
    publish_stability_analysis,
)
from alice.experiments.manifest import ArtifactManifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alice-passive-capture")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("run_dir", type=Path)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("run_dirs", nargs="+", type=Path)
    compare_parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    output = stdout or sys.stdout
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "analyze":
        analyze_passive_run(args.run_dir, stdout=output)
        return 0
    if args.command == "compare":
        analysis_manifest, generation_dir = publish_repeatability_analysis(
            args.run_dirs,
            args.output_dir,
        )
        output.write(f"repeatability\t{analysis_manifest.outcome}\t{generation_dir}\n")
        return 0
    return 1


def analyze_passive_run(
    run_dir: Path,
    *,
    stdout: TextIO | None = None,
) -> AnalysisManifest:
    output = stdout or sys.stdout
    analysis_manifest, generation_dir = publish_stability_analysis(run_dir)
    run_manifest = ArtifactManifest.model_validate_json(
        (run_dir.resolve() / "manifest.json").read_text(encoding="utf-8")
    )
    output.write(
        f"{run_manifest.run_id}\t{analysis_manifest.outcome}\t{generation_dir}\n"
    )
    return analysis_manifest
