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

from sedona_benchmark.config import BenchmarkConfig
from sedona_benchmark.manifest import git_commit, write_json


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


def _sedona_query(config: BenchmarkConfig, tier: str):
    root = config.output_root / "canonical"
    locations = sorted(root.glob("israel_locations_*.parquet"))[-1]
    roads = root / "israel_roads.parquet"
    sd = sedona.db.connect()
    sd.options.memory_limit = "12gb"
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
    target = sd.sql("SELECT COUNT(*) AS n FROM benchmark_locations").to_pandas().n[0]
    radius = int(config.values["benchmark"]["kinetica_initial_radius_m"])
    while True:
        covered = sd.sql(
            f"""
            SELECT COUNT(DISTINCT l.location_id) AS n
            FROM benchmark_locations AS l
            INNER JOIN roads AS r
              ON ST_DWithin(l.geography, r.geography, {radius})
            """
        ).to_pandas().n[0]
        if covered == target:
            break
        radius *= 2
        if radius > 1_024_000:
            raise RuntimeError("SedonaDB radius discovery exceeded 1024 km")
    sql = f"""
        WITH ranked AS (
            SELECT l.location_id, r.road_id, r.road_class,
                   ST_ToGeometry(
                       ST_ClosestPoint(r.geography, l.geography)
                   ) AS nearest_point,
                   ST_Distance(r.geography, l.geography) AS distance_m,
                   ROW_NUMBER() OVER (
                       PARTITION BY l.location_id
                       ORDER BY ST_Distance(r.geography, l.geography), r.road_id
                   ) AS nearest_rank
            FROM benchmark_locations AS l
            INNER JOIN roads AS r
              ON ST_DWithin(l.geography, r.geography, {radius})
        )
        SELECT location_id, road_id, road_class,
               nearest_point, distance_m
        FROM ranked
        WHERE nearest_rank = 1
    """
    return sd, sql, radius


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
    sd, sql, radius = _sedona_query(config, tier)
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
    oracle = _sedona_exhaustive_oracle(config, sd, exported)
    return _run_manifest(
        config=config,
        engine="sedonadb",
        tier=tier,
        result=exported,
        measurements=measurements,
        result_path=full,
        extra={"candidate_radius_m": radius, "exhaustive_oracle": oracle},
    )


def _kinetica_connection(config: BenchmarkConfig):
    import gpudb

    settings = config.values["kinetica"]
    password_path = Path(settings["password_file"])
    if password_path.stat().st_mode & 0o077:
        raise PermissionError(f"{password_path} must not be accessible by group or others")
    return gpudb.connect(
        "kinetica://",
        url=f"http://{settings['host']}:9191",
        username=settings["user"],
        password=password_path.read_text(encoding="utf-8").strip(),
        default_schema=settings["database"],
    )


def load_kinetica(config: BenchmarkConfig) -> dict[str, int]:
    """Load canonical rows through Kinetica's documented DB-API interface."""
    connection = _kinetica_connection(config)
    roads = gpd.read_parquet(
        config.output_root / "canonical" / "israel_roads.parquet"
    )
    locations_path = sorted(
        (config.output_root / "canonical").glob("israel_locations_*.parquet")
    )[-1]
    locations = gpd.read_parquet(locations_path)
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
    connection.close()
    return {"roads": len(roads), "locations": len(locations)}


def _kinetica_radius(config: BenchmarkConfig, tier: str) -> int:
    connection = _kinetica_connection(config)
    flag = {
        "arterial": "is_arterial",
        "general_driving": "is_general_driving",
        "service_rural": "is_service_rural",
    }[tier]
    target = connection.execute(
        "SELECT COUNT(*) FROM sedona_benchmark_locations"
    ).fetchone()[0]
    radius = int(config.values["benchmark"]["kinetica_initial_radius_m"])
    while True:
        covered = connection.execute(
            f"""
            SELECT COUNT(DISTINCT l.location_id)
            FROM sedona_benchmark_locations AS l
            JOIN sedona_benchmark_roads AS r
              ON r.{flag}
             AND ST_DWITHIN(l.geometry, r.geometry, {radius}, 2)
            """
        ).fetchone()[0]
        if covered == target:
            break
        radius *= 2
        if radius > 1_024_000:
            raise RuntimeError("Kinetica radius discovery exceeded 1024 km")
    connection.close()
    return radius


def run_kinetica(config: BenchmarkConfig, tier: str, smoke: bool = False) -> Path:
    repetitions = 2 if smoke else int(config.values["benchmark"]["repetitions"])
    warmups = int(config.values["benchmark"]["warmups"])
    radius = _kinetica_radius(config, tier)
    flag = {
        "arterial": "is_arterial",
        "general_driving": "is_general_driving",
        "service_rural": "is_service_rural",
    }[tier]
    sql = f"""
        SELECT location_id, road_id, road_class, nearest_point, distance_m
        FROM (
            SELECT /* KI_HINT_NO_QUERY_RESULT_CACHING */
                   l.location_id, r.road_id, r.road_class,
                   ST_CLOSESTPOINT(r.geometry, l.geometry, 2) AS nearest_point,
                   ST_DISTANCE(r.geometry, l.geometry, 2) AS distance_m,
                   ROW_NUMBER() OVER (
                       PARTITION BY l.location_id
                       ORDER BY ST_DISTANCE(r.geometry, l.geometry, 2), r.road_id
                   ) AS nearest_rank
            FROM sedona_benchmark_locations AS l
            JOIN sedona_benchmark_roads AS r
              ON r.{flag}
             AND ST_DWITHIN(l.geometry, r.geometry, {radius}, 2)
        ) AS ranked
        WHERE nearest_rank = 1
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
            rows = cursor.fetchall()
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
        extra={"candidate_radius_m": radius},
    )


def _cross_engine_correctness(config: BenchmarkConfig) -> dict[str, Any]:
    tolerance = float(config.values["benchmark"]["correctness_tolerance_m"])
    mismatches = 0
    maximum = 0.0
    checked = 0
    for tier in config.values["roads"]["tiers"]:
        run_values = []
        for path in sorted((config.output_root / "runs").glob(f"*-{tier}.json")):
            run_values.append(json.loads(path.read_text(encoding="utf-8")))
        latest = {
            engine: [run for run in run_values if run["engine"] == engine][-1]
            for engine in ("sedonadb", "kinetica")
            if any(run["engine"] == engine for run in run_values)
        }
        if set(latest) != {"sedonadb", "kinetica"}:
            continue
        left = pd.read_parquet(latest["sedonadb"]["result_path"])[
            ["location_id", "distance_m"]
        ]
        right = pd.read_parquet(latest["kinetica"]["result_path"])[
            ["location_id", "distance_m"]
        ]
        joined = left.merge(right, on="location_id", suffixes=("_sedona", "_kinetica"))
        delta = (joined.distance_m_sedona - joined.distance_m_kinetica).abs()
        checked += len(joined)
        mismatches += int((delta > tolerance).sum())
        maximum = max(maximum, float(delta.max()))
    return {
        "passed": checked > 0 and mismatches == 0,
        "checked_rows": checked,
        "mismatches": mismatches,
        "maximum_distance_delta_m": maximum,
        "tolerance_m": tolerance,
    }


def compare_results(config: BenchmarkConfig) -> Path:
    runs = []
    for path in sorted((config.output_root / "runs").glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        measured = [
            measurement["elapsed_seconds"]
            for measurement in value["measurements"]
            if measurement["kind"] == "measured"
        ]
        if not measured:
            continue
        median = statistics.median(measured)
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
                "mad_seconds": statistics.median(
                    abs(item - median) for item in measured
                ),
                "throughput_locations_s": value["location_count"] / median,
                "correctness_passed": value["correctness"]["passed"],
            }
        )
    if not runs:
        raise RuntimeError("no measured run manifests found")
    summary = pd.DataFrame(runs).sort_values(
        ["road_tier", "engine", "cpu_count"]
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
    return out
