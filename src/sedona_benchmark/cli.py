"""Command-line interface for deployment, data preparation, and benchmark runs."""

from __future__ import annotations

import argparse
import json
import os
import sys

from sedona_benchmark.benchmark import (
    compare_presentation,
    compare_results,
    load_kinetica,
    run_kinetica,
    run_sedona,
)
from sedona_benchmark.building_sources import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_WORKERS,
    analyze_building_sources,
)
from sedona_benchmark.config import load_config
from sedona_benchmark.prepare import (
    prepare_all,
    prepare_boundary,
    prepare_locations,
    prepare_roads,
)
from sedona_benchmark.presentation_bundle import build_presentation_bundle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=os.environ.get("BENCHMARK_CONFIG", "config/benchmark.yaml"),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="validate runtime and inputs")
    doctor.add_argument("--quiet", action="store_true")
    commands.add_parser("serve", help="start loopback JupyterLab")
    prepare = commands.add_parser("prepare", help="build canonical datasets")
    prepare.add_argument(
        "stage",
        choices=["all", "boundary", "roads", "locations"],
        nargs="?",
        default="all",
    )
    prepare.add_argument("--smoke", action="store_true")
    sedona = commands.add_parser("run-sedona", help="run SedonaDB")
    sedona.add_argument(
        "--tier",
        choices=["arterial", "general_driving", "service_rural"],
        default="general_driving",
    )
    sedona.add_argument("--smoke", action="store_true")
    commands.add_parser("load-kinetica", help="load canonical inputs")
    kinetica = commands.add_parser("run-kinetica", help="run Kinetica")
    kinetica.add_argument(
        "--tier",
        choices=["arterial", "general_driving", "service_rural"],
        default="general_driving",
    )
    kinetica.add_argument("--smoke", action="store_true")
    building_sources = commands.add_parser(
        "analyze-building-sources",
        help="aggregate provenance from Overture building GeoParquet",
    )
    building_sources.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"concurrent file workers (default: {DEFAULT_WORKERS})",
    )
    building_sources.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"rows per Arrow batch (default: {DEFAULT_BATCH_SIZE})",
    )
    building_sources.add_argument(
        "--rebuild",
        action="store_true",
        help="ignore matching checkpoints and rescan inputs",
    )
    building_sources.add_argument(
        "--smoke",
        action="store_true",
        help="scan one row group into an isolated incomplete report",
    )
    bundle = commands.add_parser(
        "build-presentation-bundle",
        help="build the compact Ashdod Windows presentation input",
    )
    bundle.add_argument("--output", required=True)
    commands.add_parser("compare", help="create summaries and plots")
    commands.add_parser(
        "compare-presentation",
        help="create isolated Windows presentation summaries and plots",
    )
    return parser


def doctor(config, quiet: bool = False) -> int:
    problems = []
    if not config.source_root.is_dir():
        problems.append(f"source root does not exist: {config.source_root}")
    try:
        import sedona.db

        sd = sedona.db.connect()
        sd.sql("SELECT ST_AsText(ST_Point(1, 2)) AS point").to_arrow_table()
    except Exception as error:
        problems.append(f"SedonaDB smoke query failed: {error}")
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    if not quiet:
        print(
            json.dumps(
                {"status": "ok", "source_root": str(config.source_root)}, indent=2
            )
        )
    return 0


def main() -> None:
    args = _parser().parse_args()
    config = load_config(args.config)
    if args.command == "doctor":
        raise SystemExit(doctor(config, args.quiet))
    if args.command == "serve":
        os.execvp(
            "jupyter",
            [
                "jupyter",
                "lab",
                "--ip=0.0.0.0",
                "--port=8888",
                "--no-browser",
                "--ServerApp.token=",
                "--ServerApp.password=",
            ],
        )
    if args.command == "prepare":
        if args.stage == "all":
            result = prepare_all(config, args.smoke)
        elif args.stage == "boundary":
            result = {"boundary": str(prepare_boundary(config))}
        elif args.stage == "roads":
            result = {"roads": str(prepare_roads(config))}
        else:
            result = {
                "locations": str(
                    prepare_locations(config, count=100 if args.smoke else None)
                )
            }
        print(json.dumps(result, indent=2))
    elif args.command == "run-sedona":
        print(run_sedona(config, args.tier, args.smoke))
    elif args.command == "load-kinetica":
        print(json.dumps(load_kinetica(config), indent=2))
    elif args.command == "run-kinetica":
        print(run_kinetica(config, args.tier, args.smoke))
    elif args.command == "analyze-building-sources":
        print(
            json.dumps(
                analyze_building_sources(
                    config,
                    workers=args.workers,
                    batch_size=args.batch_size,
                    rebuild=args.rebuild,
                    smoke=args.smoke,
                ),
                indent=2,
            )
        )
    elif args.command == "build-presentation-bundle":
        print(build_presentation_bundle(config, args.output))
    elif args.command == "compare":
        print(compare_results(config))
    elif args.command == "compare-presentation":
        print(compare_presentation(config))
