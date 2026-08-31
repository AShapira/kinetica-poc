"""Build a compact, attributed Ashdod presentation input bundle."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import geopandas as gpd
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from sedona_benchmark.config import BenchmarkConfig
from sedona_benchmark.manifest import file_entries, git_commit, utc_now, write_json
from sedona_benchmark.prepare import _english_common


def _dataset(root: Path, relative_glob: str) -> ds.Dataset:
    relative = Path(relative_glob)
    directory = root / relative.parent
    if not directory.is_dir():
        raise FileNotFoundError(f"presentation source directory is missing: {directory}")
    return ds.dataset(directory, format="parquet")


def _selected_boundary(
    division_rows: list[dict[str, Any]], settings: dict[str, Any]
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    locality_id = str(settings["locality_id"])
    localities = [row for row in division_rows if row["id"] == locality_id]
    countries = [
        row
        for row in division_rows
        if row["country"] == settings["country"]
        and row["subtype"] == settings["subtype"]
        and row["is_land"] is True
    ]
    if len(localities) != 1 or len(countries) != 1:
        raise RuntimeError(
            "presentation bundle requires exactly one Ashdod locality and one IL country"
        )
    locality, country = localities[0], countries[0]
    observed_name = _english_common(locality["names"])
    if (
        locality["country"] != settings["country"]
        or locality["subtype"] != "locality"
        or locality["is_land"] is not True
        or observed_name != settings["locality_name_en"]
    ):
        raise RuntimeError("configured presentation locality does not resolve to Ashdod")
    crs = settings["projected_crs"]
    locality_geometry = gpd.GeoSeries.from_wkb(
        [locality["geometry"]], crs="EPSG:4326"
    ).to_crs(crs)
    country_geometry = gpd.GeoSeries.from_wkb(
        [country["geometry"]], crs="EPSG:4326"
    ).to_crs(crs)
    selected = locality_geometry.buffer(float(settings["buffer_m"])).union_all()
    if settings.get("clip_to_country_land", True):
        selected = selected.intersection(country_geometry.union_all())
    if selected.is_empty or not selected.is_valid:
        raise RuntimeError("presentation boundary is empty or invalid")
    frame = gpd.GeoDataFrame(geometry=[selected], crs=crs).to_crs("EPSG:4326")
    return frame, {
        "locality_id": locality_id,
        "locality_name_en": observed_name,
        "country_division_id": country["id"],
        "buffer_m": float(settings["buffer_m"]),
        "clip_to_country_land": bool(settings.get("clip_to_country_land", True)),
        "projected_crs": crs,
        "area_km2": float(selected.area / 1_000_000),
    }


def build_presentation_bundle(config: BenchmarkConfig, output: str | Path) -> Path:
    """Write a new compact source bundle; existing destinations are never replaced."""
    destination = Path(output).resolve()
    if destination.exists():
        raise FileExistsError(f"presentation bundle destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    if stage.exists():
        shutil.rmtree(stage)
    try:
        settings = config.values["presentation_bundle"]
        division_dataset = _dataset(
            config.source_root, config.values["source"]["division_area_glob"]
        )
        locality_filter = ds.field("id") == str(settings["locality_id"])
        country_filter = (
            (ds.field("country") == settings["country"])
            & (ds.field("subtype") == settings["subtype"])
            & (ds.field("is_land") == True)  # noqa: E712
        )
        divisions = division_dataset.to_table(
            columns=[
                "id",
                "geometry",
                "country",
                "subtype",
                "names",
                "is_land",
            ],
            filter=locality_filter | country_filter,
        )
        boundary, boundary_metadata = _selected_boundary(
            divisions.to_pylist(), settings
        )
        xmin, ymin, xmax, ymax = (float(value) for value in boundary.total_bounds)
        classes = list(config.values["roads"]["tiers"]["general_driving"])
        segment_dataset = _dataset(
            config.source_root, config.values["source"]["segment_glob"]
        )
        segment_filter = (
            (ds.field("subtype") == "road")
            & ds.field("class").isin(classes)
            & (ds.field(("bbox", "xmin")) <= xmax)
            & (ds.field(("bbox", "xmax")) >= xmin)
            & (ds.field(("bbox", "ymin")) <= ymax)
            & (ds.field(("bbox", "ymax")) >= ymin)
        )
        segments = segment_dataset.to_table(
            columns=["id", "geometry", "subtype", "class", "bbox"],
            filter=segment_filter,
        )
        if len(segments) == 0:
            raise RuntimeError("presentation bundle contains no road candidates")
        division_path = (
            stage
            / "theme=divisions"
            / "type=division_area"
            / "part-00000.parquet"
        )
        segment_path = (
            stage
            / "theme=transportation"
            / "type=segment"
            / "part-00000.parquet"
        )
        division_path.parent.mkdir(parents=True, exist_ok=True)
        segment_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(divisions, division_path, compression="zstd")
        pq.write_table(segments, segment_path, compression="zstd")
        division_readback = pq.read_table(division_path)
        segment_readback = pq.read_table(segment_path)
        if len(division_readback) != 2 or len(segment_readback) != len(segments):
            raise RuntimeError("presentation bundle read-back count mismatch")
        files = [division_path, segment_path]
        write_json(
            stage / "bundle-manifest.json",
            {
                "schema_version": 1,
                "profile": "windows-docker-presentation",
                "complete": True,
                "created_at": utc_now(),
                "source_release": config.values["source"]["overture_release"],
                "source_config_sha256": config.sha256,
                "generator": {
                    "command": "benchmark.sh build-presentation-bundle",
                    "git_commit": git_commit(),
                    "git_dirty": os.environ.get("BENCHMARK_GIT_DIRTY") == "true",
                    "image_digest": os.environ.get("BENCHMARK_IMAGE_ID"),
                },
                "scope": {
                    **boundary_metadata,
                    "bounds": [xmin, ymin, xmax, ymax],
                    "road_tier": "general_driving",
                    "road_classes": classes,
                },
                "rows": {
                    "division_area": len(divisions),
                    "bbox_road_candidates": len(segments),
                },
                "files": file_entries(files, stage),
                "validation": {
                    "passed": True,
                    "readback_division_rows": len(division_readback),
                    "readback_segment_rows": len(segment_readback),
                },
                "attribution": (
                    "Overture Maps data; see "
                    "https://docs.overturemaps.org/attribution/"
                ),
            },
        )
        stage.rename(destination)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return destination / "bundle-manifest.json"
