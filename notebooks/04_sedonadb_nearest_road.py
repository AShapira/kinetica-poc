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

from sedona_benchmark.benchmark import _sedona_query
from sedona_benchmark.config import load_config

root = Path(os.environ.get("BENCHMARK_DATA_DIR", "/benchmark-data")) / "canonical"
roads_path = root / "israel_roads.parquet"
location_paths = sorted(root.glob("israel_locations_*.parquet"))
if not roads_path.exists() or not location_paths:
    raise FileNotFoundError("Prepare canonical roads and locations first")

config = load_config(os.environ.get("BENCHMARK_CONFIG", "config/benchmark.yaml"))
smoke = not (root / f"israel_locations_{config.values['locations']['count']}.parquet").exists()
sd, query, radius_distribution = _sedona_query(
    config, "general_driving", smoke=smoke
)
print(
    {
        "mode": "100-location smoke" if smoke else "10,000-location full",
        "radius_distribution": radius_distribution,
        "covered": sum(radius_distribution.values()),
    }
)

# %%
print(sd.sql(query).explain().to_pandas().to_string(index=False)[:8000])

# %%
sample = sd.sql(f"SELECT * FROM ({query}) WHERE location_id < 10").to_pandas(
    geometry="nearest_point"
)
sample[["location_id", "road_id", "road_class", "distance_m"]]

# %% [markdown]
# Radius discovery is outside the timed query. Each location is assigned to the
# first doubling radius that covers it, so sparse points do not force dense
# points into a 16 km candidate join. The plan shows one literal-radius spatial
# join per populated bucket, a union, and one global distance ranking.
#
# The measured runner materializes every row and hashes the ordered result.
# Timing `head()` would measure only enough work to return a display sample.
