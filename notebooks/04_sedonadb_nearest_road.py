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
# # 04 — SedonaDB nearest-road processing
#
# **Learning objectives:** understand proved-radius candidate filtering,
# prefilter the object relation correctly, calculate a closest point, and read
# the plan.
#
# `ST_KNN` is excellent for point neighbours, but Sedona documents centroid
# semantics when an input is a non-point geometry. Roads are LineStrings. This
# notebook therefore uses an exact `ST_DWithin` candidate join and distance
# ranking. The tier is materialized before radius discovery and execution.

# %%
import os
from pathlib import Path

import sedona.db

root = Path(os.environ.get("BENCHMARK_DATA_DIR", "/benchmark-data")) / "canonical"
roads_path = root / "israel_roads.parquet"
location_paths = sorted(root.glob("israel_locations_*.parquet"))
if not roads_path.exists() or not location_paths:
    raise FileNotFoundError("Prepare canonical roads and locations first")

sd = sedona.db.connect()
sd.options.memory_limit = "12gb"
sd.read_parquet(str(roads_path), partitioning=[]).to_view("all_roads")
sd.read_parquet(str(location_paths[-1]), partitioning=[]).to_view("locations")
sd.sql(
    """
    SELECT road_id, road_class, geometry,
           ST_ToGeography(geometry) AS geography
    FROM all_roads
    WHERE is_general_driving
    """
).to_memtable().to_view("roads")
sd.sql(
    """
    SELECT location_id, geometry, ST_ToGeography(geometry) AS geography
    FROM locations
    """
).to_memtable().to_view("query_locations")

# %%
target = sd.sql("SELECT COUNT(*) AS n FROM query_locations").to_pandas().n[0]
radius_m = 1000
while True:
    covered = sd.sql(
        f"""
        SELECT COUNT(DISTINCT l.location_id) AS n
        FROM query_locations l
        JOIN roads r ON ST_DWithin(l.geography, r.geography, {radius_m})
        """
    ).to_pandas().n[0]
    if covered == target:
        break
    radius_m *= 2
print({"locations": int(target), "proved_radius_m": radius_m})

# %%
query = f"""
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
  FROM query_locations l
  JOIN roads r ON ST_DWithin(l.geography, r.geography, {radius_m})
)
SELECT location_id, road_id, road_class, nearest_point, distance_m
FROM ranked
WHERE nearest_rank = 1
"""
print(sd.sql(query).explain().to_pandas().to_string(index=False)[:8000])

# %%
sample = sd.sql(f"SELECT * FROM ({query}) WHERE location_id < 10").to_pandas(
    geometry="nearest_point"
)
sample[["location_id", "road_id", "road_class", "distance_m"]]

# %% [markdown]
# Radius discovery is outside the timed query. Once each location has an
# in-radius road, an outside road cannot be nearer. `ST_Distance` ranks the
# candidates in spherical metres and `ST_ClosestPoint` identifies the answer
# on the line. These are related operations but must all be present: a road ID
# alone does not answer the “nearest point” requirement.
#
# The measured runner materializes every row and hashes the ordered result.
# Timing `head()` would measure only enough work to return a display sample.
