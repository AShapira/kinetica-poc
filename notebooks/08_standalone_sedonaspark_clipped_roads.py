# ---
# kernelspec:
#   display_name: Python 3
#   language: python
#   name: python3
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.2
# ---

# %% [markdown]
# # 08 — Standalone SedonaSpark road clipping
#
# This notebook creates a clipped roads GeoParquet dataset directly from a raw
# Overture release. It deliberately imports no project modules and reads no
# output from another notebook.
#
# The default boundary combines Overture's land-country records for Israel
# (`IL`), the West Bank (`XW`), and the Gaza Strip (`XG`). Set
# `INCLUDE_WEST_BANK_GAZA=false` to process only `IL`. These are source-data
# identifiers, not a geopolitical assertion.
#
# **Main output:** a parallel GeoParquet directory containing broad drivable
# road classes clipped exactly to the selected land boundary.

# %% [markdown]
# ## Configuration
#
# Defaults target a workstation rather than the benchmark host's full 28
# logical CPUs. Environment variables make the resource profile and paths
# adjustable without editing the processing cells.
#
# Driver memory must be configured before PySpark starts its Java process, so
# this cell intentionally runs before any PySpark or Sedona import. Run the
# notebook from a fresh kernel.

# %%
import os
import time
from pathlib import Path


def environment_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false, found {raw!r}")


INCLUDE_WEST_BANK_GAZA = environment_flag(
    "INCLUDE_WEST_BANK_GAZA",
    True,
)
LOCAL_CORES = int(os.environ.get("SEDONA_SPARK_LOCAL_CORES", "12"))
DRIVER_MEMORY = os.environ.get("SEDONA_SPARK_DRIVER_MEMORY", "32g")
PARTITIONS = int(
    os.environ.get(
        "SEDONA_SPARK_PARTITIONS",
        str(LOCAL_CORES * 2),
    )
)
OVERTURE_ROOT = Path(os.environ.get("OVERTURE_RELEASE_DIR", "/overture"))
OUTPUT_ROOT = Path(os.environ.get("BENCHMARK_DATA_DIR", "/benchmark-data"))
SPARK_LOCAL_DIR = Path(
    os.environ.get(
        "SEDONA_SPARK_LOCAL_DIR",
        str(OUTPUT_ROOT / "spark-local"),
    )
)

if LOCAL_CORES < 1:
    raise ValueError("SEDONA_SPARK_LOCAL_CORES must be at least 1")
if PARTITIONS < LOCAL_CORES:
    raise ValueError("SEDONA_SPARK_PARTITIONS must be at least the core count")

region_codes = ["IL", "XW", "XG"] if INCLUDE_WEST_BANK_GAZA else ["IL"]
scope_name = (
    "israel_west_bank_gaza"
    if INCLUDE_WEST_BANK_GAZA
    else "israel"
)
output_path = (
    OUTPUT_ROOT
    / "spark"
    / f"{scope_name}_roads.geoparquet"
)
division_path = OVERTURE_ROOT / "theme=divisions" / "type=division_area"
segment_path = OVERTURE_ROOT / "theme=transportation" / "type=segment"

SPARK_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
os.environ["SPARK_LOCAL_DIRS"] = str(SPARK_LOCAL_DIR)
os.environ["PYSPARK_SUBMIT_ARGS"] = (
    f"--driver-memory {DRIVER_MEMORY} pyspark-shell"
)

print(
    {
        "include_west_bank_gaza": INCLUDE_WEST_BANK_GAZA,
        "region_codes": region_codes,
        "local_cores": LOCAL_CORES,
        "driver_memory": DRIVER_MEMORY,
        "partitions": PARTITIONS,
        "overture_root": str(OVERTURE_ROOT),
        "output_path": str(output_path),
        "spark_local_dir": str(SPARK_LOCAL_DIR),
    }
)

# %% [markdown]
# ## Start a constrained local Spark session
#
# `local[12]` limits Spark to the configured number of concurrent local tasks.
# Twenty-four shuffle partitions provide two work units per core. Adaptive
# execution may coalesce small downstream work, while `MEMORY_AND_DISK`
# persistence allows cached geometry rows to spill instead of failing when
# memory is tight.

# %%
from pyspark import SparkContext, StorageLevel  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402
from sedona.spark import SedonaContext  # noqa: E402

if SparkContext._active_spark_context is not None:
    raise RuntimeError(
        "An active Spark context already exists. Restart the kernel so the "
        "configured cores and driver memory can take effect."
    )

expected_master = f"local[{LOCAL_CORES}]"
builder = (
    SedonaContext.builder()
    .master(expected_master)
    .appName("standalone-regional-road-clipping")
    .config("spark.driver.memory", DRIVER_MEMORY)
    .config("spark.serializer", "org.apache.spark.serializer.KryoSerializer")
    .config("spark.sql.adaptive.enabled", "true")
    .config("spark.sql.shuffle.partitions", str(PARTITIONS))
    .config("spark.ui.showConsoleProgress", "false")
)
spark = SedonaContext.create(builder.getOrCreate())
spark.sparkContext.setLogLevel("WARN")

effective_master = spark.sparkContext.master
effective_memory = spark.conf.get("spark.driver.memory")
effective_partitions = int(spark.conf.get("spark.sql.shuffle.partitions"))
if effective_master != expected_master:
    raise RuntimeError(
        f"Expected Spark master {expected_master}, found {effective_master}"
    )
if effective_memory.lower() != DRIVER_MEMORY.lower():
    raise RuntimeError(
        f"Expected driver memory {DRIVER_MEMORY}, found {effective_memory}"
    )
if effective_partitions != PARTITIONS:
    raise RuntimeError(
        f"Expected {PARTITIONS} shuffle partitions, "
        f"found {effective_partitions}"
    )

print(
    {
        "spark_version": spark.version,
        "master": effective_master,
        "driver_memory": effective_memory,
        "default_parallelism": spark.sparkContext.defaultParallelism,
        "shuffle_partitions": effective_partitions,
    }
)

# %% [markdown]
# ## Lean timing
#
# Spark transformations are lazy: elapsed time matters only after an action
# such as `count`, `collect`, or `write`. The notebook records one row count and
# one wall-clock duration for each main materialized step. It intentionally
# avoids detailed CPU, memory, and Spark-listener telemetry.

# %%
metrics: list[dict[str, int | float | str]] = []


def record_metric(
    step: str,
    rows: int,
    started: float,
) -> None:
    metrics.append(
        {
            "sequence": len(metrics) + 1,
            "step": step,
            "rows": int(rows),
            "seconds": round(time.perf_counter() - started, 3),
        }
    )


# %% [markdown]
# ## 1. Build the selected land boundary
#
# The raw Overture division dataset is filtered to land-country records. The
# selected polygons are unioned into one geometry so the roads query has one
# small boundary row to broadcast.

# %%
started = time.perf_counter()
divisions = spark.read.format("geoparquet").load(str(division_path))
selected_regions = (
    divisions.where(
        F.col("country").isin(region_codes)
        & (F.col("subtype") == "country")
        & F.col("is_land")
    )
    .select(
        "country",
        F.expr("ST_SetSRID(geometry, 4326)").alias("geometry"),
    )
    .persist(StorageLevel.MEMORY_AND_DISK)
)
selected_region_count = selected_regions.count()
actual_codes = {
    row.country
    for row in selected_regions.select("country").collect()
}
if actual_codes != set(region_codes):
    raise RuntimeError(
        f"Expected boundary codes {region_codes}, found {sorted(actual_codes)}"
    )

boundary = (
    selected_regions.agg(
        F.expr("ST_Union_Agg(geometry)").alias("boundary_geometry")
    )
    .persist(StorageLevel.MEMORY_AND_DISK)
)
if boundary.count() != 1:
    raise RuntimeError("The union boundary must contain exactly one row")

bounds = (
    boundary.selectExpr(
        "ST_XMin(boundary_geometry) AS xmin",
        "ST_YMin(boundary_geometry) AS ymin",
        "ST_XMax(boundary_geometry) AS xmax",
        "ST_YMax(boundary_geometry) AS ymax",
    )
    .first()
    .asDict()
)
record_metric(
    "boundary selection and union",
    selected_region_count,
    started,
)
print("Boundary bounds:", bounds)

# %% [markdown]
# ## 2. Prune the global transportation scan
#
# A segment is a bbox candidate when its envelope overlaps the selected
# boundary envelope in both axes:
#
# ```text
# segment.xmin <= boundary.xmax
# segment.xmax >= boundary.xmin
# segment.ymin <= boundary.ymax
# segment.ymax >= boundary.ymin
# ```
#
# These four scalar comparisons reduce work but do not prove that a road is
# inside the boundary. Exact geometry predicates follow in the next step.

# %%
road_classes = [
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "track",
]

started = time.perf_counter()
segments = spark.read.format("geoparquet").load(str(segment_path))
candidates = (
    segments.select("id", "class", "subtype", "bbox", "geometry")
    .where(
        (F.col("subtype") == "road")
        & F.col("class").isin(road_classes)
        & (F.col("bbox.xmin") <= bounds["xmax"])
        & (F.col("bbox.xmax") >= bounds["xmin"])
        & (F.col("bbox.ymin") <= bounds["ymax"])
        & (F.col("bbox.ymax") >= bounds["ymin"])
    )
    .repartition(PARTITIONS)
    .persist(StorageLevel.MEMORY_AND_DISK)
)
candidate_count = candidates.count()
record_metric(
    "bbox filtering and repartitioning",
    candidate_count,
    started,
)

# %% [markdown]
# ## 3. Clip roads exactly and expand multipart results
#
# The single boundary row is broadcast to every task. `ST_Intersects` removes
# bbox false positives, and `ST_Intersection` clips crossing roads at the
# boundary. Intersection can create multipart geometry, so
# `ST_CollectionExtract(..., 2)` keeps line components and `ST_Dump` emits one
# output row per LineString.

# %%
started = time.perf_counter()
clipped = (
    candidates.crossJoin(F.broadcast(boundary))
    .where(F.expr("ST_Intersects(geometry, boundary_geometry)"))
    .select(
        F.col("id").alias("source_segment_id"),
        F.col("class").alias("road_class"),
        F.expr(
            "ST_CollectionExtract("
            "ST_Intersection(geometry, boundary_geometry), 2)"
        ).alias("clipped_geometry"),
    )
)
roads = (
    clipped.selectExpr(
        "source_segment_id",
        "road_class",
        "posexplode(ST_Dump(clipped_geometry)) "
        "AS (part_number, geometry)",
    )
    .where(
        "geometry IS NOT NULL "
        "AND NOT ST_IsEmpty(geometry) "
        "AND ST_IsValid(geometry) "
        "AND GeometryType(geometry) = 'LINESTRING' "
        "AND ST_Length(geometry) > 0"
    )
    .select(
        F.concat_ws(
            "#",
            F.col("source_segment_id"),
            F.col("part_number"),
        ).alias("road_id"),
        "source_segment_id",
        "road_class",
        F.expr("ST_SetSRID(geometry, 4326)").alias("geometry"),
    )
    .persist(StorageLevel.MEMORY_AND_DISK)
)
road_count = roads.count()
record_metric(
    "exact clipping and LineString expansion",
    road_count,
    started,
)
candidates.unpersist()
print("Final Spark partitions:", roads.rdd.getNumPartitions())

# %% [markdown]
# ## 4. Write GeoParquet 1.1
#
# Spark writes a directory of part files in parallel. This is faster and more
# natural than forcing one worker to create a single file. GeoParquet 1.1
# covering mode adds `geometry_bbox`, allowing future readers to prune data
# spatially.

# %%
started = time.perf_counter()
(
    roads.write.format("geoparquet")
    .mode("overwrite")
    .option("compression", "zstd")
    .option("geoparquet.version", "1.1.0")
    .option("geoparquet.covering.mode", "auto")
    .save(str(output_path))
)
record_metric("GeoParquet write", road_count, started)

# %% [markdown]
# ## 5. Read back and validate
#
# One aggregate action checks the output count, geometry type, validity,
# positive length, SRID, and generated bbox column. This avoids several
# separate validation scans.

# %%
started = time.perf_counter()
written = spark.read.format("geoparquet").load(str(output_path))
if "geometry_bbox" not in written.columns:
    raise RuntimeError("GeoParquet output is missing geometry_bbox")

validation = written.agg(
    F.count("*").alias("row_count"),
    F.sum(
        F.when(
            F.expr(
                "geometry IS NULL "
                "OR ST_IsEmpty(geometry) "
                "OR NOT ST_IsValid(geometry) "
                "OR GeometryType(geometry) <> 'LINESTRING' "
                "OR ST_Length(geometry) <= 0 "
                "OR ST_SRID(geometry) <> 4326 "
                "OR geometry_bbox IS NULL"
            ),
            1,
        ).otherwise(0)
    ).alias("invalid_rows"),
    F.collect_set(F.expr("GeometryType(geometry)")).alias(
        "geometry_types"
    ),
    F.collect_set(F.expr("ST_SRID(geometry)")).alias("srids"),
).first()
output_count = int(validation.row_count)
record_metric("output readback validation", output_count, started)

if output_count != road_count:
    raise RuntimeError(
        f"Wrote {road_count} rows but read back {output_count}"
    )
if int(validation.invalid_rows) != 0:
    raise RuntimeError(
        f"GeoParquet validation found {validation.invalid_rows} invalid rows"
    )
if set(validation.geometry_types) != {"LINESTRING"}:
    raise RuntimeError(
        f"Unexpected geometry types: {validation.geometry_types}"
    )
if set(validation.srids) != {4326}:
    raise RuntimeError(f"Unexpected SRIDs: {validation.srids}")

print(
    {
        "output_path": str(output_path),
        "rows": output_count,
        "geometry_types": validation.geometry_types,
        "srids": validation.srids,
        "covering_column": "geometry_bbox",
    }
)

# %% [markdown]
# ## Performance summary
#
# These are end-to-end action times on the current workstation. They are not a
# hardware-neutral benchmark and should be remeasured after changing cores,
# memory, partitions, source release, or selected boundary.

# %%
spark.createDataFrame(metrics).orderBy("sequence").select(
    "step",
    "rows",
    "seconds",
).show(truncate=False)

roads.unpersist()
boundary.unpersist()
selected_regions.unpersist()
spark.stop()
