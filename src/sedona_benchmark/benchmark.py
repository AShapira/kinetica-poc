"""Benchmark execution, correctness, summaries, and plots."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import statistics
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import psutil
import sedona.db
from shapely import from_wkt

from sedona_benchmark.config import BenchmarkConfig
from sedona_benchmark.manifest import git_commit, sha256_file, write_json
from sedona_benchmark.paths import canonical_paths, dataset_manifest_names


class _ProcessTelemetry:
    """Low-frequency process telemetry for a fully materialized query."""

    def __init__(self, interval_seconds: float):
        self.interval_seconds = interval_seconds
        self.process = psutil.Process()
        self.samples: list[dict[str, Any]] = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.io_before = self.process.io_counters()._asdict()

    def _record(self, include_cpu: bool = True) -> None:
        memory = self.process.memory_info()
        self.samples.append(
            {
                "elapsed_seconds": time.perf_counter() - self.started,
                "process_cpu_percent": (
                    self.process.cpu_percent(interval=None) if include_cpu else None
                ),
                "system_cpu_percent": (
                    psutil.cpu_percent(interval=None) if include_cpu else None
                ),
                "rss_bytes": memory.rss,
                "vms_bytes": memory.vms,
            }
        )

    def _sample(self) -> None:
        self.process.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None)
        self._record(include_cpu=False)
        while not self.stop_event.wait(self.interval_seconds):
            self._record()

    def __enter__(self):
        self.started = time.perf_counter()
        self.thread.start()
        return self

    def __exit__(self, *_args) -> None:
        self.stop_event.set()
        self.thread.join()
        self._record()

    def summary(self) -> dict[str, Any]:
        io_after = self.process.io_counters()._asdict()
        return {
            "scope": "benchmark_client_process",
            "sample_interval_seconds": self.interval_seconds,
            "sample_count": len(self.samples),
            "maximum_rss_bytes": max(item["rss_bytes"] for item in self.samples),
            "maximum_process_cpu_percent": max(
                item["process_cpu_percent"]
                for item in self.samples
                if item["process_cpu_percent"] is not None
            ),
            "read_bytes": io_after["read_bytes"] - self.io_before["read_bytes"],
            "write_bytes": io_after["write_bytes"] - self.io_before["write_bytes"],
            "samples": self.samples,
        }


def _environment(engine: str) -> dict[str, Any]:
    process = psutil.Process()
    environment = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "logical_cpus": psutil.cpu_count(),
        "physical_cpus": psutil.cpu_count(logical=False),
        "memory_bytes": psutil.virtual_memory().total,
        "cpu_affinity": process.cpu_affinity(),
        "git_commit": git_commit(),
        "git_dirty": os.environ.get("BENCHMARK_GIT_DIRTY") == "true",
        "image_digest": os.environ.get("BENCHMARK_IMAGE_ID"),
        "external_telemetry_dir": os.environ.get(
            "BENCHMARK_EXTERNAL_TELEMETRY_DIR"
        ),
        "benchmark_profile": os.environ.get("BENCHMARK_PROFILE", "production"),
        "offline_mode": os.environ.get("BENCHMARK_OFFLINE") == "true",
        "container_cpu_limit": os.environ.get("BENCHMARK_CONTAINER_CPUS"),
        "container_memory_limit": os.environ.get("BENCHMARK_CONTAINER_MEMORY"),
        "sedonadb_memory_limit": os.environ.get("SEDONADB_MEMORY_LIMIT"),
        "bundle_sha256": os.environ.get("BENCHMARK_BUNDLE_SHA256"),
        "kinetica_image": os.environ.get("BENCHMARK_KINETICA_IMAGE"),
        "kinetica_image_digest": os.environ.get(
            "BENCHMARK_KINETICA_IMAGE_DIGEST"
        ),
        "kinetica_gpu_mode": os.environ.get("BENCHMARK_KINETICA_GPU_MODE"),
        "kinetica_gpu_name": os.environ.get("BENCHMARK_KINETICA_GPU_NAME"),
    }
    if engine == "sedonadb":
        import sedonadb

        environment["sedonadb"] = sedonadb.__version__
    return environment


def _checksum(rows: pd.DataFrame) -> str:
    stable = rows.sort_values("location_id").to_csv(
        index=False, float_format="%.9f"
    )
    return hashlib.sha256(stable.encode()).hexdigest()


def _normalise_result(result: gpd.GeoDataFrame | pd.DataFrame) -> pd.DataFrame:
    normalised = pd.DataFrame(result.drop(columns="nearest_point"))
    if hasattr(result, "nearest_point"):
        normalised["nearest_point_wkt"] = result.nearest_point.to_wkt(
            rounding_precision=9
        )
    return normalised


def _sedona_query(config: BenchmarkConfig, tier: str, smoke: bool = False):
    paths = canonical_paths(config)
    locations = (
        canonical_paths(config, 100).locations
        if smoke
        else paths.locations
    )
    if not locations.exists():
        raise FileNotFoundError(f"canonical locations are missing: {locations}")
    roads = paths.roads
    sd = sedona.db.connect()
    sd.options.memory_limit = str(
        config.values.get("runtime", {}).get("sedonadb_memory_limit", "12gb")
    )
    sd.options.memory_pool_type = "fair"
    sd.options.temp_dir = str(config.output_root / "spill")
    sd.read_parquet(str(locations), partitioning=[]).to_view("locations")
    sd.read_parquet(str(roads), partitioning=[]).to_view("all_roads")
    flag = {
        "arterial": "is_arterial",
        "general_driving": "is_general_driving",
        "service_rural": "is_service_rural",
    }[tier]
    sd.sql(
        f"""
        SELECT road_id, road_class, geometry,
               ST_ToGeography(geometry) AS geography
        FROM all_roads
        WHERE {flag}
        """
    ).to_memtable().to_view("roads")
    sd.sql(
        """
        SELECT location_id, geometry,
               ST_ToGeography(geometry) AS geography
        FROM locations
        """
    ).to_memtable().to_view("benchmark_locations")
    all_ids = set(
        int(value)
        for value in sd.sql(
            "SELECT location_id FROM benchmark_locations"
        ).to_pandas().location_id
    )
    unresolved = set(all_ids)
    assignments: list[dict[str, int]] = []
    distribution: dict[int, int] = {}
    radius = int(config.values["benchmark"]["kinetica_initial_radius_m"])
    while unresolved:
        sd.create_data_frame(
            pd.DataFrame({"location_id": sorted(unresolved)})
        ).to_view("unresolved_ids", overwrite=True)
        covered = {
            int(value)
            for value in sd.sql(
            f"""
            SELECT DISTINCT l.location_id
            FROM benchmark_locations AS l
            INNER JOIN unresolved_ids AS u
              ON l.location_id = u.location_id
            INNER JOIN roads AS r
              ON ST_DWithin(l.geography, r.geography, {radius})
            """
            ).to_pandas().location_id
        }
        for location_id in sorted(covered):
            assignments.append({"location_id": location_id, "radius_m": radius})
        if covered:
            distribution[radius] = len(covered)
        unresolved -= covered
        radius *= 2
        if unresolved and radius > 1_024_000:
            raise RuntimeError("SedonaDB radius discovery exceeded 1024 km")
    sd.create_data_frame(pd.DataFrame(assignments)).to_view("location_radii")
    sd.sql(
        """
        SELECT l.location_id, l.geometry, l.geography, b.radius_m
        FROM benchmark_locations AS l
        INNER JOIN location_radii AS b
          ON l.location_id = b.location_id
        """
    ).to_memtable().to_view("bucketed_locations")
    candidate_branches = []
    for bucket_radius in sorted(distribution):
        candidate_branches.append(
            f"""
            SELECT l.location_id, r.road_id, r.road_class,
                   l.geography AS location_geography,
                   r.geography AS road_geography
            FROM (
                SELECT * FROM bucketed_locations
                WHERE radius_m = {bucket_radius}
            ) AS l
            INNER JOIN roads AS r
              ON ST_DWithin(l.geography, r.geography, {bucket_radius})
            """
        )
    candidates = "\nUNION ALL\n".join(candidate_branches)
    sql = f"""
        WITH candidates AS (
            {candidates}
        ),
        ranked AS (
            SELECT location_id, road_id, road_class,
                   ST_ToGeometry(
                       ST_ClosestPoint(road_geography, location_geography)
                   ) AS nearest_point,
                   ST_Distance(
                       road_geography, location_geography
                   ) AS distance_m,
                   ROW_NUMBER() OVER (
                       PARTITION BY location_id
                       ORDER BY ST_Distance(
                           road_geography, location_geography
                       ), road_id
                   ) AS nearest_rank
            FROM candidates
        )
        SELECT location_id, road_id, road_class,
               nearest_point, distance_m
        FROM ranked
        WHERE nearest_rank = 1
    """
    return sd, sql, distribution


def _sedona_exhaustive_oracle(
    config: BenchmarkConfig,
    sd,
    result: gpd.GeoDataFrame,
) -> dict[str, Any]:
    """Check a fixed prefix against an exhaustive all-roads distance ranking."""
    count = min(
        int(config.values["benchmark"]["exhaustive_oracle_locations"]), len(result)
    )
    oracle = sd.sql(
        f"""
        WITH exhaustive AS (
            SELECT l.location_id, r.road_id,
                   ST_Distance(r.geography, l.geography) AS distance_m,
                   ROW_NUMBER() OVER (
                       PARTITION BY l.location_id
                       ORDER BY ST_Distance(r.geography, l.geography), r.road_id
                   ) AS nearest_rank
            FROM (
                SELECT * FROM benchmark_locations ORDER BY location_id LIMIT {count}
            ) AS l
            CROSS JOIN roads AS r
        )
        SELECT location_id, road_id, distance_m
        FROM exhaustive
        WHERE nearest_rank = 1
        """
    ).to_pandas()
    observed = pd.DataFrame(result)[["location_id", "road_id", "distance_m"]]
    joined = observed.merge(
        oracle,
        on="location_id",
        suffixes=("_observed", "_oracle"),
        validate="one_to_one",
    )
    delta = (joined.distance_m_observed - joined.distance_m_oracle).abs()
    tolerance = float(config.values["benchmark"]["correctness_tolerance_m"])
    return {
        "passed": bool(
            len(joined) == count
            and (delta <= tolerance).all()
            and (joined.road_id_observed == joined.road_id_oracle).all()
        ),
        "checked_locations": len(joined),
        "distance_mismatches": int((delta > tolerance).sum()),
        "road_id_mismatches": int(
            (joined.road_id_observed != joined.road_id_oracle).sum()
        ),
        "maximum_distance_delta_m": float(delta.max()) if len(delta) else None,
        "tolerance_m": tolerance,
    }


def _run_manifest(
    *,
    config: BenchmarkConfig,
    engine: str,
    tier: str,
    result: pd.DataFrame,
    measurements: list[dict[str, Any]],
    result_path: Path,
    extra: dict[str, Any] | None = None,
) -> Path:
    warmups = int(config.values["benchmark"]["warmups"])
    repetitions = len(
        [measurement for measurement in measurements if measurement["kind"] == "measured"]
    )
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{engine}-{tier}"
    extra = extra or {}
    oracle = extra.get("exhaustive_oracle")
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "engine": engine,
        "road_tier": tier,
        "location_count": len(result),
        "warmups": warmups,
        "repetitions": repetitions,
        "environment": _environment(engine),
        "measurements": measurements,
        "correctness": {
            "passed": bool(
                result.location_id.is_unique
                and result.distance_m.notna().all()
                and (oracle is None or oracle["passed"])
            ),
            "unique_locations": bool(result.location_id.is_unique),
            "null_distances": int(result.distance_m.isna().sum()),
        },
        "result_path": str(result_path),
    }
    manifest.update(extra)
    out = config.output_root / "runs" / f"{run_id}.json"
    write_json(out, manifest)
    return out


def run_sedona(config: BenchmarkConfig, tier: str, smoke: bool = False) -> Path:
    repetitions = 2 if smoke else int(config.values["benchmark"]["repetitions"])
    warmups = int(config.values["benchmark"]["warmups"])
    sd, sql, radius_distribution = _sedona_query(config, tier, smoke)
    plan = sd.sql(sql).explain().to_pandas().to_string(index=False)
    plan_path = (
        Path(config.values["output"]["evidence_root"])
        / "plans"
        / f"sedonadb-{tier}.txt"
    )
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan + "\n", encoding="utf-8")
    measurements: list[dict[str, Any]] = []
    exported: gpd.GeoDataFrame | None = None
    for number in range(1 + warmups + repetitions):
        kind = (
            "cold"
            if number == 0
            else ("warmup" if number <= warmups else "measured")
        )
        started = time.perf_counter()
        with _ProcessTelemetry(
            float(config.values["benchmark"]["telemetry_interval_seconds"])
        ) as telemetry:
            result = sd.sql(sql).to_pandas(geometry="nearest_point")
        elapsed = time.perf_counter() - started
        checksum = _checksum(_normalise_result(result))
        measurements.append(
            {
                "kind": kind,
                "sequence": number,
                "elapsed_seconds": elapsed,
                "result_count": len(result),
                "result_checksum": checksum,
                "process_rss_bytes": psutil.Process().memory_info().rss,
                "telemetry": telemetry.summary(),
            }
        )
        exported = result
    assert exported is not None
    full = (
        config.output_root
        / "results"
        / f"sedonadb-{tier}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.parquet"
    )
    full.parent.mkdir(parents=True, exist_ok=True)
    exported.to_parquet(full, index=False, compression="zstd")
    affinity_count = len(psutil.Process().cpu_affinity())
    if smoke or affinity_count == psutil.cpu_count():
        oracle = _sedona_exhaustive_oracle(config, sd, exported)
    else:
        oracle = {
            "passed": True,
            "skipped": True,
            "reason": (
                "scaling-only case; exhaustive oracle runs on the full-resource "
                "reference for this identical tier and canonical dataset"
            ),
        }
    return _run_manifest(
        config=config,
        engine="sedonadb",
        tier=tier,
        result=exported,
        measurements=measurements,
        result_path=full,
        extra={
            "candidate_radius_distribution": {
                str(radius): count
                for radius, count in sorted(radius_distribution.items())
            },
            "exhaustive_oracle": oracle,
        },
    )


def _kinetica_connection(config: BenchmarkConfig):
    import gpudb

    settings = config.values["kinetica"]
    password_path = Path(settings["password_file"])
    if password_path.stat().st_mode & 0o077:
        raise PermissionError(f"{password_path} must not be accessible by group or others")
    connection = gpudb.connect(
        "kinetica://",
        url=f"http://{settings['host']}:{int(settings.get('rest_port', 9191))}",
        username=settings["user"],
        password=password_path.read_text(encoding="utf-8").strip(),
    )
    current_schema = connection.execute("SELECT CURRENT_SCHEMA").fetchone()[0]
    if current_schema != settings["database"]:
        connection.close()
        raise RuntimeError(
            f"expected Kinetica schema {settings['database']}, found {current_schema}"
        )
    return connection


def _fetch_all_kinetica(cursor, batch_size: int = 5_000) -> list[Any]:
    """Consume every DB-API result page.

    The pinned Kinetica cursor's ``fetchall()`` is implemented as one
    ``fetchmany(cursor.arraysize)`` call and therefore returns at most its
    default 5,000 rows. Explicit paging keeps this benchmark independent of
    that client default.
    """
    rows: list[Any] = []
    while batch := cursor.fetchmany(batch_size):
        rows.extend(batch)
    return rows


def load_kinetica(config: BenchmarkConfig) -> dict[str, int]:
    """Load canonical rows through Kinetica's documented DB-API interface."""
    connection = _kinetica_connection(config)
    paths = canonical_paths(config)
    roads = gpd.read_parquet(paths.roads)
    locations = gpd.read_parquet(paths.locations)
    connection.execute("DROP TABLE IF EXISTS sedona_benchmark_roads")
    connection.execute("DROP TABLE IF EXISTS sedona_benchmark_locations")
    connection.execute(
        """
        CREATE TABLE sedona_benchmark_roads (
            road_id VARCHAR(256) NOT NULL,
            road_class VARCHAR(32) NOT NULL,
            is_arterial BOOLEAN NOT NULL,
            is_general_driving BOOLEAN NOT NULL,
            is_service_rural BOOLEAN NOT NULL,
            geometry GEOMETRY NOT NULL
        ) USING TABLE PROPERTIES (NO_ERROR_IF_EXISTS=TRUE)
        """
    )
    connection.execute(
        """
        CREATE TABLE sedona_benchmark_locations (
            location_id BIGINT NOT NULL,
            geometry GEOMETRY NOT NULL
        ) USING TABLE PROPERTIES (NO_ERROR_IF_EXISTS=TRUE)
        """
    )
    road_rows = [
        [
            row.road_id,
            row.road_class,
            bool(row.is_arterial),
            bool(row.is_general_driving),
            bool(row.is_service_rural),
            row.geometry.wkt,
        ]
        for row in roads.itertuples(index=False)
    ]
    location_rows = [
        [int(row.location_id), row.geometry.wkt]
        for row in locations.itertuples(index=False)
    ]
    connection.executemany(
        """
        INSERT INTO sedona_benchmark_roads
          (road_id, road_class, is_arterial, is_general_driving,
           is_service_rural, geometry)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        road_rows,
    )
    connection.executemany(
        "INSERT INTO sedona_benchmark_locations (location_id, geometry) VALUES ($1, $2)",
        location_rows,
    )
    loaded_roads = int(
        connection.execute(
            "SELECT COUNT(*) FROM sedona_benchmark_roads"
        ).fetchone()[0]
    )
    loaded_locations = int(
        connection.execute(
            "SELECT COUNT(*) FROM sedona_benchmark_locations"
        ).fetchone()[0]
    )
    connection.close()
    expected = {"roads": len(roads), "locations": len(locations)}
    actual = {"roads": loaded_roads, "locations": loaded_locations}
    if actual != expected:
        raise RuntimeError(
            f"Kinetica load verification failed: expected {expected}, found {actual}"
        )
    return actual


def _kinetica_radius_buckets(
    config: BenchmarkConfig, tier: str
) -> tuple[dict[int, int], str]:
    connection = _kinetica_connection(config)
    solution = int(config.values["benchmark"]["kinetica_solution"])
    flag = {
        "arterial": "is_arterial",
        "general_driving": "is_general_driving",
        "service_rural": "is_service_rural",
    }[tier]
    all_ids = {
        int(row[0])
        for row in _fetch_all_kinetica(
            connection.execute(
                """
                SELECT location_id
                FROM sedona_benchmark_locations
                ORDER BY location_id
                """
            )
        )
    }
    unresolved = set(all_ids)
    assignments: list[list[int]] = []
    distribution: dict[int, int] = {}
    radius = int(config.values["benchmark"]["kinetica_initial_radius_m"])
    while unresolved:
        unresolved_sql = ", ".join(str(value) for value in sorted(unresolved))
        covered = {
            int(row[0])
            for row in _fetch_all_kinetica(
                connection.execute(
                    f"""
                    SELECT DISTINCT l.location_id
                    FROM sedona_benchmark_locations AS l
                    JOIN sedona_benchmark_roads AS r
                      ON r.{flag}
                     AND ST_DWITHIN(
                         l.geometry, r.geometry, {radius}, {solution}
                     )
                    WHERE l.location_id IN ({unresolved_sql})
                    ORDER BY l.location_id
                    """
                )
            )
        }
        assignments.extend([[location_id, radius] for location_id in sorted(covered)])
        if covered:
            distribution[radius] = len(covered)
        unresolved -= covered
        radius *= 2
        if unresolved and radius > 1_024_000:
            raise RuntimeError("Kinetica radius discovery exceeded 1024 km")
    table = f"sedona_benchmark_location_radii_{tier}"
    connection.execute(f"DROP TABLE IF EXISTS {table}")
    connection.execute(
        f"""
        CREATE TABLE {table} (
            location_id BIGINT NOT NULL,
            radius_m INTEGER NOT NULL
        ) USING TABLE PROPERTIES (NO_ERROR_IF_EXISTS=TRUE)
        """
    )
    connection.executemany(
        f"INSERT INTO {table} (location_id, radius_m) VALUES ($1, $2)",
        assignments,
    )
    connection.close()
    return distribution, table


def run_kinetica(config: BenchmarkConfig, tier: str, smoke: bool = False) -> Path:
    repetitions = 2 if smoke else int(config.values["benchmark"]["repetitions"])
    warmups = int(config.values["benchmark"]["warmups"])
    radius_distribution, radius_table = _kinetica_radius_buckets(config, tier)
    solution = int(config.values["benchmark"]["kinetica_solution"])
    flag = {
        "arterial": "is_arterial",
        "general_driving": "is_general_driving",
        "service_rural": "is_service_rural",
    }[tier]
    candidate_branches = []
    for radius in sorted(radius_distribution):
        candidate_branches.append(
            f"""
            SELECT /* KI_HINT_NO_QUERY_RESULT_CACHING */
                   l.location_id, r.road_id, r.road_class,
                   r.geometry AS road_geometry,
                   l.geometry AS location_geometry
            FROM sedona_benchmark_locations AS l
            JOIN {radius_table} AS b
              ON l.location_id = b.location_id
             AND b.radius_m = {radius}
            JOIN sedona_benchmark_roads AS r
              ON r.{flag}
             AND ST_DWITHIN(
                 l.geometry, r.geometry, {radius}, {solution}
             )
            """
        )
    candidates = "\nUNION ALL\n".join(candidate_branches)
    sql = f"""
        SELECT location_id, road_id, road_class, nearest_point, distance_m
        FROM (
            SELECT location_id, road_id, road_class,
                   ST_CLOSESTPOINT(
                       road_geometry, location_geometry, {solution}
                   ) AS nearest_point,
                   ST_DISTANCE(
                       road_geometry, location_geometry, {solution}
                   ) AS distance_m,
                   ROW_NUMBER() OVER (
                       PARTITION BY location_id
                       ORDER BY ST_DISTANCE(
                           road_geometry, location_geometry, {solution}
                       ), road_id
                   ) AS nearest_rank
            FROM (
                {candidates}
            ) AS candidates
        ) AS ranked
        WHERE nearest_rank = 1
        ORDER BY location_id
    """
    plan_path = (
        Path(config.values["output"]["evidence_root"])
        / "plans"
        / f"kinetica-{tier}.txt"
    )
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("EXPLAIN " + sql + "\n", encoding="utf-8")
    connection = _kinetica_connection(config)
    measurements: list[dict[str, Any]] = []
    exported: pd.DataFrame | None = None
    for number in range(1 + warmups + repetitions):
        kind = (
            "cold"
            if number == 0
            else ("warmup" if number <= warmups else "measured")
        )
        started = time.perf_counter()
        with _ProcessTelemetry(
            float(config.values["benchmark"]["telemetry_interval_seconds"])
        ) as telemetry:
            cursor = connection.execute(sql)
            rows = _fetch_all_kinetica(cursor)
        elapsed = time.perf_counter() - started
        result = pd.DataFrame(
            rows,
            columns=[
                "location_id",
                "road_id",
                "road_class",
                "nearest_point_wkt",
                "distance_m",
            ],
        )
        measurements.append(
            {
                "kind": kind,
                "sequence": number,
                "elapsed_seconds": elapsed,
                "result_count": len(result),
                "result_checksum": _checksum(result),
                "telemetry": telemetry.summary(),
            }
        )
        exported = result
    connection.close()
    assert exported is not None
    full = (
        config.output_root
        / "results"
        / f"kinetica-{tier}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.parquet"
    )
    full.parent.mkdir(parents=True, exist_ok=True)
    exported.to_parquet(full, index=False, compression="zstd")
    return _run_manifest(
        config=config,
        engine="kinetica",
        tier=tier,
        result=exported,
        measurements=measurements,
        result_path=full,
        extra={
            "candidate_radius_distribution": {
                str(radius): count
                for radius, count in sorted(radius_distribution.items())
            }
        },
    )


def _publication_runs(config: BenchmarkConfig) -> list[dict[str, Any]]:
    """Select one clean, complete five-repetition run per comparable case."""
    expected_locations = int(config.values["locations"]["count"])
    expected_repetitions = int(config.values["benchmark"]["repetitions"])
    latest: dict[tuple[str, str, tuple[int, ...]], dict[str, Any]] = {}
    for path in sorted((config.output_root / "runs").glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        affinity = tuple(value["environment"].get("cpu_affinity", []))
        if (
            value.get("location_count") != expected_locations
            or value.get("repetitions") != expected_repetitions
            or not value.get("correctness", {}).get("passed")
            or value["environment"].get("git_dirty")
        ):
            continue
        key = (value["engine"], value["road_tier"], affinity)
        if key not in latest or value["created_at"] > latest[key]["created_at"]:
            value["_manifest_path"] = str(path)
            latest[key] = value
    return sorted(
        latest.values(),
        key=lambda value: (
            value["road_tier"],
            value["engine"],
            len(value["environment"].get("cpu_affinity", [])),
        ),
    )


def _full_resource_run(
    runs: list[dict[str, Any]], engine: str, tier: str
) -> dict[str, Any] | None:
    matches = [
        run
        for run in runs
        if run["engine"] == engine
        and run["road_tier"] == tier
        and len(run["environment"].get("cpu_affinity", []))
        == run["environment"].get("logical_cpus")
    ]
    return max(matches, key=lambda run: run["created_at"], default=None)


def _projected_points(values) -> gpd.GeoSeries:
    return gpd.GeoSeries(values, crs="EPSG:4326").to_crs("EPSG:2039")


def _selected_engine_run(
    runs: list[dict[str, Any]],
    engine: str,
    tier: str,
    *,
    require_full_resource: bool,
) -> dict[str, Any] | None:
    if require_full_resource:
        return _full_resource_run(runs, engine, tier)
    matches = [
        run
        for run in runs
        if run["engine"] == engine and run["road_tier"] == tier
    ]
    return max(matches, key=lambda run: run["created_at"], default=None)


def _cross_engine_correctness(
    config: BenchmarkConfig,
    *,
    runs: list[dict[str, Any]] | None = None,
    tiers_to_check: list[str] | None = None,
    require_full_resource: bool = True,
) -> dict[str, Any]:
    tolerance = float(config.values["benchmark"]["correctness_tolerance_m"])
    expected = int(config.values["locations"]["count"])
    runs = runs if runs is not None else _publication_runs(config)
    tiers_to_check = tiers_to_check or list(config.values["roads"]["tiers"])
    roads = gpd.read_parquet(canonical_paths(config).roads).set_index("road_id").geometry
    tiers: dict[str, Any] = {}
    mismatch_samples: list[dict[str, Any]] = []
    for tier in tiers_to_check:
        sedona_run = _selected_engine_run(
            runs,
            "sedonadb",
            tier,
            require_full_resource=require_full_resource,
        )
        kinetica_run = _selected_engine_run(
            runs,
            "kinetica",
            tier,
            require_full_resource=require_full_resource,
        )
        if sedona_run is None or kinetica_run is None:
            reason = (
                "missing full-resource run"
                if require_full_resource
                else "missing presentation run"
            )
            tiers[tier] = {"passed": False, "reason": reason}
            continue
        left = gpd.read_parquet(sedona_run["result_path"])[
            ["location_id", "road_id", "nearest_point", "distance_m"]
        ]
        right = pd.read_parquet(kinetica_run["result_path"])[
            ["location_id", "road_id", "nearest_point_wkt", "distance_m"]
        ]
        joined = left.merge(right, on="location_id", suffixes=("_sedona", "_kinetica"))
        distance_delta = (
            joined.distance_m_sedona - joined.distance_m_kinetica
        ).abs()
        sedona_points = _projected_points(joined.nearest_point.to_list())
        kinetica_points = _projected_points(
            from_wkt(joined.nearest_point_wkt.to_numpy())
        )
        point_delta = sedona_points.distance(kinetica_points)
        sedona_roads = _projected_points(
            joined.road_id_sedona.map(roads).to_list()
        )
        kinetica_roads = _projected_points(
            joined.road_id_kinetica.map(roads).to_list()
        )
        sedona_off_road = sedona_points.distance(sedona_roads)
        kinetica_off_road = kinetica_points.distance(kinetica_roads)
        road_id_differs = joined.road_id_sedona != joined.road_id_kinetica
        unexplained_road_id = road_id_differs & (
            (distance_delta > tolerance)
            | (point_delta > tolerance)
            | (sedona_off_road > tolerance)
            | (kinetica_off_road > tolerance)
        )
        failed = (
            (distance_delta > tolerance)
            | (point_delta > tolerance)
            | (sedona_off_road > tolerance)
            | (kinetica_off_road > tolerance)
            | unexplained_road_id
        )
        for index in joined.index[failed][:100]:
            mismatch_samples.append(
                {
                    "tier": tier,
                    "location_id": int(joined.at[index, "location_id"]),
                    "sedona_road_id": joined.at[index, "road_id_sedona"],
                    "kinetica_road_id": joined.at[index, "road_id_kinetica"],
                    "distance_delta_m": float(distance_delta.at[index]),
                    "nearest_point_delta_m": float(point_delta.at[index]),
                    "sedona_point_off_road_m": float(sedona_off_road.at[index]),
                    "kinetica_point_off_road_m": float(kinetica_off_road.at[index]),
                }
            )
        oracle = sedona_run.get("exhaustive_oracle", {})
        complete = (
            len(left) == expected
            and left.location_id.is_unique
            and len(right) == expected
            and right.location_id.is_unique
            and len(joined) == expected
        )
        tiers[tier] = {
            "passed": bool(complete and not failed.any() and oracle.get("passed")),
            "checked_rows": len(joined),
            "complete": complete,
            "distance_mismatches": int((distance_delta > tolerance).sum()),
            "maximum_distance_delta_m": float(distance_delta.max()),
            "nearest_point_mismatches": int((point_delta > tolerance).sum()),
            "maximum_nearest_point_delta_m": float(point_delta.max()),
            "sedona_points_off_reported_road": int(
                (sedona_off_road > tolerance).sum()
            ),
            "maximum_sedona_point_off_road_m": float(sedona_off_road.max()),
            "kinetica_points_off_reported_road": int(
                (kinetica_off_road > tolerance).sum()
            ),
            "maximum_kinetica_point_off_road_m": float(kinetica_off_road.max()),
            "different_road_ids": int(road_id_differs.sum()),
            "unexplained_different_road_ids": int(unexplained_road_id.sum()),
            "sedona_exhaustive_oracle": oracle,
            "sedona_run_id": sedona_run["run_id"],
            "kinetica_run_id": kinetica_run["run_id"],
        }
    checked = sum(value.get("checked_rows", 0) for value in tiers.values())
    passed = (
        set(tiers) == set(tiers_to_check)
        and checked == expected * len(tiers_to_check)
        and all(value.get("passed") for value in tiers.values())
    )
    return {
        "passed": passed,
        "checked_rows": checked,
        "expected_rows": expected * len(tiers_to_check),
        "tolerance_m": tolerance,
        "tiers": tiers,
        "mismatch_samples": mismatch_samples,
    }


def _gpu_summary(
    config: BenchmarkConfig, value: dict[str, Any]
) -> dict[str, float | None]:
    directory = value["environment"].get("external_telemetry_dir")
    if not directory:
        return {
            "gpu_utilization_peak_percent": None,
            "gpu_utilization_mean_percent": None,
            "gpu_memory_peak_mib": None,
        }
    path = Path(directory) / "nvidia-smi.csv"
    if not path.exists():
        path = (
            config.output_root
            / "telemetry"
            / Path(directory).name
            / "nvidia-smi.csv"
        )
    if not path.exists():
        return {
            "gpu_utilization_peak_percent": None,
            "gpu_utilization_mean_percent": None,
            "gpu_memory_peak_mib": None,
        }
    telemetry = pd.read_csv(path)
    telemetry.columns = telemetry.columns.str.strip()
    utilization = telemetry["utilization.gpu [%]"].str.rstrip(" %").astype(float)
    memory = telemetry["memory.used [MiB]"].str.rstrip(" MiB").astype(float)
    return {
        "gpu_utilization_peak_percent": float(utilization.max()),
        "gpu_utilization_mean_percent": float(utilization.mean()),
        "gpu_memory_peak_mib": float(memory.max()),
    }


def compare_results(config: BenchmarkConfig) -> Path:
    runs = []
    for value in _publication_runs(config):
        measured = [
            measurement["elapsed_seconds"]
            for measurement in value["measurements"]
            if measurement["kind"] == "measured"
        ]
        median = statistics.median(measured)
        mad = statistics.median(abs(item - median) for item in measured)
        telemetry = [measurement["telemetry"] for measurement in value["measurements"]]
        runs.append(
            {
                "run_id": value["run_id"],
                "engine": value["engine"],
                "road_tier": value["road_tier"],
                "cpu_count": len(value["environment"].get("cpu_affinity", [])),
                "location_count": value["location_count"],
                "median_seconds": median,
                "p95_seconds": pd.Series(measured).quantile(0.95),
                "min_seconds": min(measured),
                "max_seconds": max(measured),
                "mad_seconds": mad,
                "relative_mad": mad / median,
                "high_variability": (
                    mad / median
                    > float(
                        config.values["benchmark"][
                            "high_variability_relative_mad"
                        ]
                    )
                ),
                "throughput_locations_s": value["location_count"] / median,
                "correctness_passed": value["correctness"]["passed"],
                "speedup": None,
                "parallel_efficiency": None,
                "client_peak_rss_bytes": max(
                    item["maximum_rss_bytes"] for item in telemetry
                ),
                "client_peak_cpu_percent": max(
                    item["maximum_process_cpu_percent"] for item in telemetry
                ),
                "git_commit": value["environment"]["git_commit"],
                "image_digest": value["environment"]["image_digest"],
                "candidate_radius_distribution": json.dumps(
                    value.get("candidate_radius_distribution", {}),
                    sort_keys=True,
                ),
                **_gpu_summary(config, value),
            }
        )
    if not runs:
        raise RuntimeError("no measured run manifests found")
    summary = pd.DataFrame(runs).sort_values(
        ["road_tier", "engine", "cpu_count"]
    )
    scaling_mask = (summary.engine == "sedonadb") & (
        summary.road_tier == config.values["roads"]["headline_tier"]
    )
    scaling = summary[scaling_mask]
    one_cpu = scaling[scaling.cpu_count == 1]
    if len(one_cpu) == 1:
        baseline = float(one_cpu.iloc[0].median_seconds)
        summary.loc[scaling_mask, "speedup"] = (
            baseline / summary.loc[scaling_mask, "median_seconds"]
        )
        summary.loc[scaling_mask, "parallel_efficiency"] = (
            summary.loc[scaling_mask, "speedup"]
            / summary.loc[scaling_mask, "cpu_count"]
        )
    evidence = Path(config.values["output"]["evidence_root"])
    evidence.mkdir(parents=True, exist_ok=True)
    out = evidence / "benchmark-summary.csv"
    summary.to_csv(out, index=False, float_format="%.6f")
    correctness = _cross_engine_correctness(config)
    write_json(evidence / "correctness-summary.json", correctness)
    plots = evidence / "plots"
    plots.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(8, 5))
    for tier, group in summary[summary.engine == "sedonadb"].groupby("road_tier"):
        axis.plot(group.cpu_count, group.median_seconds, marker="o", label=tier)
    axis.set(
        xlabel="Available logical CPUs",
        ylabel="Median warm latency (seconds)",
        title="SedonaDB nearest-road scaling",
    )
    axis.grid(True, alpha=0.3)
    axis.legend()
    figure.tight_layout()
    figure.savefig(plots / "sedonadb-scaling.svg")
    figure.savefig(plots / "sedonadb-scaling.png", dpi=160)
    plt.close(figure)
    full_resource = summary[
        summary.cpu_count
        == summary.groupby(["engine", "road_tier"]).cpu_count.transform("max")
    ]
    comparison = full_resource.pivot(
        index="road_tier", columns="engine", values="median_seconds"
    )
    figure, axis = plt.subplots(figsize=(8, 5))
    comparison.plot.bar(ax=axis)
    axis.set(
        xlabel="Road tier",
        ylabel="Median warm latency (seconds)",
        title="Nearest-road latency on the deployed SedonaDB and Kinetica systems",
    )
    axis.grid(True, axis="y", alpha=0.3)
    figure.tight_layout()
    figure.savefig(plots / "engine-comparison.svg")
    figure.savefig(plots / "engine-comparison.png", dpi=160)
    plt.close(figure)
    dataset_manifests = {}
    boundary_name, roads_name, location_name = dataset_manifest_names(config)
    for name in (boundary_name, roads_name):
        path = config.output_root / "manifests" / name
        dataset_manifests[name] = {
            "sha256": sha256_file(path),
            "value": json.loads(path.read_text(encoding="utf-8")),
        }
    location_path = config.output_root / "manifests" / location_name
    dataset_manifests[location_name] = {
        "sha256": sha256_file(location_path),
        "value": json.loads(location_path.read_text(encoding="utf-8")),
    }
    artifact_paths = [
        out,
        evidence / "correctness-summary.json",
        plots / "sedonadb-scaling.svg",
        plots / "sedonadb-scaling.png",
        plots / "engine-comparison.svg",
        plots / "engine-comparison.png",
    ]
    write_json(
        evidence / "publication-manifest.json",
        {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "source_release": config.values["source"]["overture_release"],
            "config_sha256": config.sha256,
            "selection_policy": (
                "latest clean, correct, configured-location, configured-repetition run "
                "per engine, tier, and exact CPU affinity"
            ),
            "dataset_manifests": dataset_manifests,
            "selected_runs": [
                {
                    "run_id": value["run_id"],
                    "manifest_path": value["_manifest_path"],
                    "engine": value["engine"],
                    "road_tier": value["road_tier"],
                    "cpu_affinity": value["environment"].get("cpu_affinity", []),
                    "git_commit": value["environment"]["git_commit"],
                    "image_digest": value["environment"]["image_digest"],
                    "result_checksums": sorted(
                        {
                            measurement["result_checksum"]
                            for measurement in value["measurements"]
                        }
                    ),
                }
                for value in _publication_runs(config)
            ],
            "artifacts": [
                {
                    "path": str(path.relative_to(evidence)),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in artifact_paths
            ],
            "correctness_passed": correctness["passed"],
        },
    )
    return out


def _presentation_runs(config: BenchmarkConfig) -> list[dict[str, Any]]:
    expected_locations = int(config.values["locations"]["count"])
    expected_repetitions = int(config.values["benchmark"]["repetitions"])
    expected_tier = config.values["roads"]["headline_tier"]
    latest: dict[str, dict[str, Any]] = {}
    for path in sorted((config.output_root / "runs").glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        environment = value.get("environment", {})
        if (
            environment.get("benchmark_profile")
            != "windows-docker-presentation"
            or value.get("location_count") != expected_locations
            or value.get("repetitions") != expected_repetitions
            or value.get("road_tier") != expected_tier
            or not value.get("correctness", {}).get("passed")
        ):
            continue
        engine = value["engine"]
        if engine not in latest or value["created_at"] > latest[engine]["created_at"]:
            value["_manifest_path"] = str(path)
            latest[engine] = value
    runs = [latest[name] for name in ("sedonadb", "kinetica") if name in latest]
    bundle_hashes = {
        run["environment"].get("bundle_sha256") for run in runs
    }
    if len(runs) == 2 and (None in bundle_hashes or len(bundle_hashes) != 1):
        raise RuntimeError("presentation engine runs do not use one bundle hash")
    return runs


def compare_presentation(config: BenchmarkConfig) -> Path:
    """Create explicitly non-publication output for the Windows presentation."""
    runs = _presentation_runs(config)
    if {run["engine"] for run in runs} != {"sedonadb", "kinetica"}:
        raise RuntimeError("one valid SedonaDB and Kinetica presentation run is required")
    rows: list[dict[str, Any]] = []
    for value in runs:
        measured = [
            measurement["elapsed_seconds"]
            for measurement in value["measurements"]
            if measurement["kind"] == "measured"
        ]
        median = statistics.median(measured)
        mad = statistics.median(abs(item - median) for item in measured)
        telemetry = [measurement["telemetry"] for measurement in value["measurements"]]
        cpu_limit = value["environment"].get("container_cpu_limit")
        rows.append(
            {
                "run_id": value["run_id"],
                "engine": value["engine"],
                "road_tier": value["road_tier"],
                "location_count": value["location_count"],
                "container_cpu_limit": float(cpu_limit) if cpu_limit else None,
                "container_memory_limit": value["environment"].get(
                    "container_memory_limit"
                ),
                "median_seconds": median,
                "p95_seconds": pd.Series(measured).quantile(0.95),
                "min_seconds": min(measured),
                "max_seconds": max(measured),
                "mad_seconds": mad,
                "relative_mad": mad / median,
                "throughput_locations_s": value["location_count"] / median,
                "correctness_passed": value["correctness"]["passed"],
                "client_peak_rss_bytes": max(
                    item["maximum_rss_bytes"] for item in telemetry
                ),
                "git_commit": value["environment"].get("git_commit"),
                "benchmark_image_digest": value["environment"].get(
                    "image_digest"
                ),
                "kinetica_image_digest": value["environment"].get(
                    "kinetica_image_digest"
                ),
                "bundle_sha256": value["environment"].get("bundle_sha256"),
            }
        )
    evidence = Path(config.values["output"]["evidence_root"])
    evidence.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame(rows).sort_values("engine")
    out = evidence / "presentation-summary.csv"
    summary.to_csv(out, index=False, float_format="%.6f")
    tier = config.values["roads"]["headline_tier"]
    correctness = _cross_engine_correctness(
        config,
        runs=runs,
        tiers_to_check=[tier],
        require_full_resource=False,
    )
    write_json(evidence / "correctness-summary.json", correctness)
    if not correctness["passed"]:
        raise RuntimeError("presentation cross-engine correctness failed")
    plots = evidence / "plots"
    plots.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(7, 4.5))
    summary.plot.bar(
        x="engine",
        y="median_seconds",
        legend=False,
        color=["#4c78a8", "#f58518"],
        ax=axis,
    )
    axis.set(
        xlabel="Engine",
        ylabel="Median measured latency (seconds)",
        title="Ashdod presentation: nearest general-driving road",
    )
    axis.grid(True, axis="y", alpha=0.3)
    figure.tight_layout()
    svg = plots / "engine-comparison.svg"
    png = plots / "engine-comparison.png"
    figure.savefig(svg)
    figure.savefig(png, dpi=160)
    plt.close(figure)
    boundary_name, roads_name, location_name = dataset_manifest_names(config)
    dataset_manifests: dict[str, Any] = {}
    for name in (boundary_name, roads_name, location_name):
        path = config.output_root / "manifests" / name
        dataset_manifests[name] = {
            "sha256": sha256_file(path),
            "value": json.loads(path.read_text(encoding="utf-8")),
        }
    artifact_paths = [out, evidence / "correctness-summary.json", svg, png]
    write_json(
        evidence / "presentation-manifest.json",
        {
            "schema_version": 1,
            "purpose": "presentation",
            "comparable_to_publication": False,
            "created_at": datetime.now(UTC).isoformat(),
            "profile": "windows-docker-presentation",
            "scope": "ashdod-10km-land-clipped",
            "source_release": config.values["source"]["overture_release"],
            "config_sha256": config.sha256,
            "bundle_sha256": runs[0]["environment"]["bundle_sha256"],
            "limitations": [
                "Not a production or publication benchmark.",
                "Uses 1,000 presentation points and one road tier.",
                "Docker Desktop and RTX 5080 are not certified Kinetica topology.",
                "Container CPU quotas are not physical-core affinity cases.",
            ],
            "dataset_manifests": dataset_manifests,
            "selected_runs": [
                {
                    "run_id": run["run_id"],
                    "manifest_path": run["_manifest_path"],
                    "engine": run["engine"],
                    "environment": run["environment"],
                }
                for run in runs
            ],
            "artifacts": [
                {
                    "path": str(path.relative_to(evidence)),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in artifact_paths
            ],
            "correctness_passed": correctness["passed"],
        },
    )
    return out
