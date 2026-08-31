"""Stable dataset paths for production and scoped presentation runs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sedona_benchmark.config import BenchmarkConfig

_SAFE_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def scope_slug(config: BenchmarkConfig) -> str:
    slug = str(config.values.get("scope", {}).get("slug", "israel"))
    if not _SAFE_SLUG.fullmatch(slug):
        raise ValueError("scope.slug must contain lowercase letters, digits, and hyphens")
    return slug


@dataclass(frozen=True)
class CanonicalPaths:
    boundary: Path
    roads: Path
    locations: Path


def canonical_paths(
    config: BenchmarkConfig, count: int | None = None
) -> CanonicalPaths:
    root = config.output_root / "canonical"
    slug = scope_slug(config)
    location_count = count or int(config.values["locations"]["count"])
    return CanonicalPaths(
        boundary=root / f"{slug}_boundary.parquet",
        roads=root / f"{slug}_roads.parquet",
        locations=root / f"{slug}_locations_{location_count}.parquet",
    )


def dataset_manifest_names(config: BenchmarkConfig) -> tuple[str, str, str]:
    slug = scope_slug(config)
    count = int(config.values["locations"]["count"])
    return (
        f"{slug}_boundary.json",
        f"{slug}_roads.json",
        f"{slug}_locations_{count}.json",
    )
