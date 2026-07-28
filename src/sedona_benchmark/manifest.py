"""Dataset and run evidence helpers."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd

from sedona_benchmark.config import BenchmarkConfig


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit() -> str | None:
    supplied = os.environ.get("BENCHMARK_GIT_COMMIT")
    if supplied:
        return supplied
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def file_entries(paths: Iterable[Path], relative_to: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": str(path.relative_to(relative_to)),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(paths)
    ]


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def dataset_manifest(
    *,
    config: BenchmarkConfig,
    dataset_id: str,
    stage: str,
    files: list[Path],
    frame: gpd.GeoDataFrame,
    command: str,
    duration_seconds: float,
    parents: list[str] | None = None,
    statistics: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    geometry_types = sorted(frame.geometry.geom_type.dropna().unique().tolist())
    return {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "stage": stage,
        "created_at": utc_now(),
        "generator": {
            "command": command,
            "config_sha256": config.sha256,
            "git_commit": git_commit(),
            "git_dirty": os.environ.get("BENCHMARK_GIT_DIRTY") == "true",
            "image_digest": os.environ.get("BENCHMARK_IMAGE_ID"),
        },
        "source_release": config.values["source"]["overture_release"],
        "parents": parents or [],
        "crs": str(frame.crs),
        "geometry_type": ",".join(geometry_types),
        "row_count": len(frame),
        "files": file_entries(files, config.output_root),
        "bounds": [float(item) for item in frame.total_bounds],
        "statistics": statistics or {},
        "duration_seconds": duration_seconds,
        "validation": validation or {"passed": True},
        "license": "Overture Maps data; see https://docs.overturemaps.org/attribution/",
    }
