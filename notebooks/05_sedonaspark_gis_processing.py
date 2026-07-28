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
# # 05 — SedonaSpark GIS processing
#
# SedonaSpark is included to learn distributed spatial data handling. It is not
# a third headline benchmark result because its execution model and non-point
# KNN behavior differ from SedonaDB's measured line-distance workflow.
#
# **Learning objectives:** create a Sedona Spark session, inspect partitions,
# read GeoParquet, register spatial SQL, and perform exact predicates.

# %%
import os
from pathlib import Path

from sedona.spark import SedonaContext

builder = (
    SedonaContext.builder()
    .master(os.environ.get("SPARK_MASTER", "local[*]"))
    .appName("sedona-israel-gis-learning")
    .config("spark.serializer", "org.apache.spark.serializer.KryoSerializer")
)
spark = SedonaContext.create(builder.getOrCreate())
print("Spark:", spark.version)

# %%
root = Path(os.environ.get("BENCHMARK_DATA_DIR", "/benchmark-data")) / "canonical"
roads_path = root / "israel_roads.parquet"
if not roads_path.exists():
    raise FileNotFoundError("Prepare canonical roads first")
roads = spark.read.format("geoparquet").load(str(roads_path))
roads.createOrReplaceTempView("roads")
print("Partitions:", roads.rdd.getNumPartitions())
spark.sql(
    """
    SELECT road_class, COUNT(*) AS fragments
    FROM roads
    WHERE is_general_driving
    GROUP BY road_class
    ORDER BY fragments DESC
    """
).show(truncate=False)

# %% [markdown]
# ## Partitioning is part of semantics and performance
#
# Spark schedules partitions, not individual Python rows. Too few partitions
# leave cores idle; too many create scheduler and shuffle overhead. Spatial
# partitioning additionally affects candidate replication around boundaries.
# Inspect physical plans rather than assuming a SQL function implies an index.

# %%
spark.sql(
    """
    SELECT road_id, road_class, ST_IsValid(geometry) AS valid
    FROM roads
    WHERE is_general_driving
    LIMIT 10
    """
).explain(mode="formatted")

# %% [markdown]
# **Exercise:** compare `repartition(4)` and `repartition(28)` for a class
# aggregation. That is a Spark scheduling exercise, not a replacement for the
# controlled SedonaDB CPU-affinity benchmark.
#
# Always stop the session before opening another kernel in the official
# one-worker image.

# %%
spark.stop()
