"""Canonical road and deterministic location preparation."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import sedona.db
from shapely.geometry import LineString, MultiLineString, Point

from sedona_benchmark.config import BenchmarkConfig
from sedona_benchmark.manifest import dataset_manifest, write_json
from sedona_benchmark.paths import canonical_paths, dataset_manifest_names, scope_slug


def _refresh_full_dataset_index(config: BenchmarkConfig) -> dict[str, str] | None:
    canonical = canonical_paths(config)
    paths = {
        "boundary": canonical.boundary,
        "roads": canonical.roads,
        "locations": canonical.locations,
    }
    if not all(path.exists() for path in paths.values()):
        return None
    index = {name: str(path) for name, path in paths.items()}
    write_json(config.output_root / "manifests" / "dataset-index.json", index)
    return index


def _connect(config: BenchmarkConfig):
    sd = sedona.db.connect()
    sd.options.memory_limit = str(
        config.values.get("runtime", {}).get("sedonadb_memory_limit", "12gb")
    )
    sd.options.memory_pool_type = "fair"
    spill = config.output_root / "spill"
    spill.mkdir(parents=True, exist_ok=True)
    sd.options.temp_dir = str(spill)
    return sd


def _write_geoparquet(frame: gpd.GeoDataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(
        path,
        index=False,
        compression="zstd",
        schema_version="1.1.0",
        write_covering_bbox=True,
    )


def _english_common(names: Any) -> str | None:
    if not isinstance(names, dict):
        return None
    common = names.get("common")
    if isinstance(common, dict):
        return common.get("en")
    if isinstance(common, list):
        return dict(common).get("en")
    return None


def prepare_boundary(config: BenchmarkConfig) -> Path:
    started = time.perf_counter()
    out = canonical_paths(config).boundary
    files = config.source_glob("division_area_glob")
    sd = _connect(config)
    sd.read_parquet([str(path) for path in files], partitioning=[]).to_view("division_areas")
    settings = config.values["boundary"]
    country = settings["country"].replace("'", "''")
    subtype = settings["subtype"].replace("'", "''")
    mode = settings.get("mode", "country")
    if mode == "country":
        query = f"""
            SELECT id AS division_id, country, subtype, is_land, geometry
            FROM division_areas
            WHERE country = '{country}'
              AND subtype = '{subtype}'
              AND is_land = true
        """
        result = sd.sql(query).to_pandas(geometry="geometry")
        if len(result) != 1:
            raise RuntimeError(
                f"expected exactly one {country} land country division, "
                f"found {len(result)}"
            )
        result = result.set_crs("EPSG:4326", allow_override=True)
        boundary_statistics: dict[str, Any] = {
            "division_id": str(result.iloc[0]["division_id"]),
            "mode": mode,
            "source_file_count": len(files),
        }
    elif mode == "locality_buffer":
        locality_id = str(settings["locality_id"]).replace("'", "''")
        locality = sd.sql(
            f"""
            SELECT id AS division_id, country, subtype, is_land, names, geometry
            FROM division_areas
            WHERE id = '{locality_id}'
            """
        ).to_pandas(geometry="geometry")
        if len(locality) != 1:
            raise RuntimeError(
                f"expected exactly one locality {locality_id}, found {len(locality)}"
            )
        expected_name = settings.get("locality_name_en")
        observed_name = _english_common(locality.iloc[0]["names"])
        if expected_name and observed_name != expected_name:
            raise RuntimeError(
                f"expected locality English name {expected_name!r}, "
                f"found {observed_name!r}"
            )
        if (
            locality.iloc[0]["country"] != settings["country"]
            or locality.iloc[0]["subtype"] != "locality"
            or not bool(locality.iloc[0]["is_land"])
        ):
            raise RuntimeError("configured locality is not the expected IL land locality")
        country_frame = sd.sql(
            f"""
            SELECT id AS division_id, country, subtype, is_land, geometry
            FROM division_areas
            WHERE country = '{country}'
              AND subtype = '{subtype}'
              AND is_land = true
            """
        ).to_pandas(geometry="geometry")
        if len(country_frame) != 1:
            raise RuntimeError(
                f"expected exactly one {country} land country division, "
                f"found {len(country_frame)}"
            )
        projected_crs = settings["projected_crs"]
        locality_metric = locality.set_crs(
            "EPSG:4326", allow_override=True
        ).to_crs(projected_crs)
        country_metric = country_frame.set_crs(
            "EPSG:4326", allow_override=True
        ).to_crs(projected_crs)
        buffer_m = float(settings["buffer_m"])
        selected = locality_metric.geometry.buffer(buffer_m).union_all()
        if settings.get("clip_to_country_land", True):
            selected = selected.intersection(country_metric.geometry.union_all())
        if selected.is_empty or not selected.is_valid:
            raise RuntimeError("buffered locality boundary is empty or invalid")
        result = gpd.GeoDataFrame(
            {
                "division_id": [locality.iloc[0]["division_id"]],
                "country": [settings["country"]],
                "subtype": ["locality_buffer"],
                "is_land": [True],
            },
            geometry=[selected],
            crs=projected_crs,
        ).to_crs("EPSG:4326")
        boundary_statistics = {
            "division_id": str(locality.iloc[0]["division_id"]),
            "country_division_id": str(country_frame.iloc[0]["division_id"]),
            "locality_name_en": observed_name,
            "mode": mode,
            "buffer_m": buffer_m,
            "clip_to_country_land": bool(
                settings.get("clip_to_country_land", True)
            ),
            "area_km2": float(
                result.to_crs(projected_crs).geometry.area.sum() / 1_000_000
            ),
            "source_file_count": len(files),
        }
    else:
        raise ValueError(f"unsupported boundary.mode: {mode}")
    _write_geoparquet(result, out)
    boundary_name, _, _ = dataset_manifest_names(config)
    slug = scope_slug(config)
    manifest = dataset_manifest(
        config=config,
        dataset_id=(
            f"overture-{config.values['source']['overture_release']}-{slug}-boundary-v1"
        ),
        stage="canonical",
        files=[out],
        frame=result,
        command="benchmark.sh prepare boundary",
        duration_seconds=time.perf_counter() - started,
        statistics=boundary_statistics,
        validation={
            "passed": bool(
                result.geometry.is_valid.all() and not result.geometry.is_empty.any()
            )
        },
    )
    write_json(config.output_root / "manifests" / boundary_name, manifest)
    _refresh_full_dataset_index(config)
    return out


def _line_parts(geometry) -> list[LineString]:
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)
    if geometry is None or geometry.is_empty:
        return []
    return [part for part in getattr(geometry, "geoms", []) if isinstance(part, LineString)]


def prepare_roads(config: BenchmarkConfig, boundary_path: Path | None = None) -> Path:
    started = time.perf_counter()
    paths = canonical_paths(config)
    boundary_path = boundary_path or paths.boundary
    if not boundary_path.exists():
        boundary_path = prepare_boundary(config)
    boundary = gpd.read_parquet(boundary_path)
    xmin, ymin, xmax, ymax = (float(value) for value in boundary.total_bounds)
    all_classes = config.values["roads"]["tiers"]["service_rural"]
    class_sql = ", ".join("'" + value.replace("'", "''") + "'" for value in all_classes)
    raw = config.output_root / "intermediate" / "bbox_clipped_roads.parquet"
    raw.parent.mkdir(parents=True, exist_ok=True)
    segment_files = config.source_glob("segment_glob")
    sd = _connect(config)
    sd.read_parquet([str(path) for path in segment_files], partitioning=[]).to_view("segments")
    sd.read_parquet(str(boundary_path), partitioning=[]).to_view("benchmark_boundary")
    query = f"""
        SELECT s.id AS source_segment_id, s.class AS road_class,
               ST_Intersection(s.geometry, b.geometry) AS geometry
        FROM segments AS s
        CROSS JOIN benchmark_boundary AS b
        WHERE s.subtype = 'road'
          AND s.class IN ({class_sql})
          AND s.bbox.xmin <= {xmax}
          AND s.bbox.xmax >= {xmin}
          AND s.bbox.ymin <= {ymax}
          AND s.bbox.ymax >= {ymin}
          AND ST_Intersects(s.geometry, b.geometry)
    """
    planned = sd.sql(query)
    plan_path = Path(config.values["output"]["evidence_root"]) / "plans" / "bbox-road-scan.txt"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        planned.explain().to_pandas().to_string(index=False) + "\n", encoding="utf-8"
    )
    planned.to_parquet(
        raw,
        single_file_output=True,
        geoparquet_version="1.1",
        max_row_group_size=config.values["roads"]["max_row_group_size"],
        compression="zstd(6)",
    )
    clipped = gpd.read_parquet(raw).set_crs("EPSG:4326", allow_override=True)
    records: list[dict[str, Any]] = []
    tier_sets = {
        name: set(values) for name, values in config.values["roads"]["tiers"].items()
    }
    for row in clipped.itertuples(index=False):
        for part_number, part in enumerate(_line_parts(row.geometry)):
            if part.is_empty or not part.is_valid or part.length == 0:
                continue
            records.append(
                {
                    "road_id": f"{row.source_segment_id}#{part_number}",
                    "source_segment_id": row.source_segment_id,
                    "road_class": row.road_class,
                    "is_arterial": row.road_class in tier_sets["arterial"],
                    "is_general_driving": row.road_class in tier_sets["general_driving"],
                    "is_service_rural": row.road_class in tier_sets["service_rural"],
                    "geometry": part,
                }
            )
    roads = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
    roads = roads.sort_values(["road_class", "road_id"]).reset_index(drop=True)
    metric = roads.to_crs(config.values["boundary"]["projected_crs"])
    roads["length_m"] = metric.length.astype(float)
    out = paths.roads
    _write_geoparquet(roads, out)
    classes = {
        str(key): int(value)
        for key, value in roads["road_class"].value_counts().sort_index().items()
    }
    tier_counts = {name: int(roads[f"is_{name}"].sum()) for name in tier_sets}
    _, roads_name, _ = dataset_manifest_names(config)
    slug = scope_slug(config)
    manifest = dataset_manifest(
        config=config,
        dataset_id=(
            f"overture-{config.values['source']['overture_release']}-{slug}-roads-v1"
        ),
        stage="canonical",
        files=[out],
        frame=roads,
        command="benchmark.sh prepare roads",
        duration_seconds=time.perf_counter() - started,
        parents=[f"{slug}-boundary"],
        statistics={
            "source_file_count": len(segment_files),
            "bbox": [xmin, ymin, xmax, ymax],
            "class_counts": classes,
            "tier_counts": tier_counts,
            "total_length_m": float(roads.length_m.sum()),
            "bbox_predicate": (
                "xmin<=scope.xmax AND xmax>=scope.xmin AND "
                "ymin<=scope.ymax AND ymax>=scope.ymin"
            ),
        },
        validation={
            "passed": bool(
                len(roads) > 0
                and roads.geometry.is_valid.all()
                and not roads.geometry.is_empty.any()
            ),
            "all_classes_expected": bool(set(roads.road_class).issubset(set(all_classes))),
        },
    )
    write_json(config.output_root / "manifests" / roads_name, manifest)
    _write_geoparquet(
        roads.head(100),
        Path(config.values["output"]["evidence_root"])
        / "samples"
        / f"{slug}_roads_100.parquet",
    )
    _refresh_full_dataset_index(config)
    return out


def prepare_locations(
    config: BenchmarkConfig,
    boundary_path: Path | None = None,
    count: int | None = None,
) -> Path:
    started = time.perf_counter()
    paths = canonical_paths(config, count)
    boundary_path = boundary_path or canonical_paths(config).boundary
    boundary = gpd.read_parquet(boundary_path).to_crs(
        config.values["boundary"]["projected_crs"]
    )
    land = boundary.geometry.union_all()
    target = count or int(config.values["locations"]["count"])
    seed = int(config.values["locations"]["seed"])
    rng = np.random.Generator(np.random.PCG64(seed))
    xmin, ymin, xmax, ymax = land.bounds
    points: list[Point] = []
    while len(points) < target:
        batch = max(1024, (target - len(points)) * 2)
        xs = rng.uniform(xmin, xmax, batch)
        ys = rng.uniform(ymin, ymax, batch)
        points.extend(point for point in map(Point, xs, ys) if land.contains(point))
    frame = gpd.GeoDataFrame(
        {"location_id": np.arange(target, dtype=np.int64), "seed": seed},
        geometry=points[:target],
        crs=config.values["boundary"]["projected_crs"],
    ).to_crs("EPSG:4326")
    frame["longitude"] = frame.geometry.x
    frame["latitude"] = frame.geometry.y
    frame = frame[["location_id", "seed", "longitude", "latitude", "geometry"]]
    out = paths.locations
    _write_geoparquet(frame, out)
    csv_path = out.with_suffix(".csv")
    frame.drop(columns="geometry").assign(wkt=frame.geometry.to_wkt()).to_csv(
        csv_path, index=False, float_format="%.12f"
    )
    slug = scope_slug(config)
    manifest = dataset_manifest(
        config=config,
        dataset_id=f"{slug}-locations-n{target}-seed{seed}-v1",
        stage="canonical",
        files=[out, csv_path],
        frame=frame,
        command="benchmark.sh prepare locations",
        duration_seconds=time.perf_counter() - started,
        parents=[f"{slug}-boundary"],
        statistics={
            "seed": seed,
            "generator": "numpy.PCG64",
            "projected_crs": str(boundary.crs),
        },
        validation={
            "passed": bool(frame.location_id.is_unique and len(frame) == target),
            "unique": bool(frame.geometry.to_wkb().is_unique),
            "contained": True,
        },
    )
    write_json(
        config.output_root / "manifests" / f"{slug}_locations_{target}.json",
        manifest,
    )
    _write_geoparquet(
        frame.head(100),
        Path(config.values["output"]["evidence_root"])
        / "samples"
        / f"{slug}_locations_100.parquet",
    )
    if target == int(config.values["locations"]["count"]):
        _refresh_full_dataset_index(config)
    return out


def prepare_all(config: BenchmarkConfig, smoke: bool = False) -> dict[str, str]:
    config.output_root.mkdir(parents=True, exist_ok=True)
    boundary = prepare_boundary(config)
    roads = prepare_roads(config, boundary)
    locations = prepare_locations(config, boundary, 100 if smoke else None)
    index = {
        "boundary": str(boundary),
        "roads": str(roads),
        "locations": str(locations),
    }
    if smoke:
        write_json(
            config.output_root / "manifests" / "smoke-dataset-index.json", index
        )
        _refresh_full_dataset_index(config)
    else:
        write_json(config.output_root / "manifests" / "dataset-index.json", index)
    return index
