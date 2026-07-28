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
# # 06 — Kinetica equivalent workflow
#
# Kinetica does not expose SedonaDB's exact KNN join interface. Equivalence is
# achieved with a candidate radius followed by exact distance ranking.
#
# **Learning objectives:** prove the radius is sufficient, understand why
# radius discovery is preparation, and separate GPU visibility from GPU query
# utilization.

# %%
import os
from pathlib import Path

import pandas as pd
import yaml

repo_root = Path(
    os.environ.get(
        "BENCHMARK_REPO_DIR",
        Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd(),
    )
)
config = yaml.safe_load((repo_root / "config" / "benchmark.yaml").read_text())
pd.Series(
    {
        "initial_radius_m": config["benchmark"]["kinetica_initial_radius_m"],
        "distance_solution": "1 — Haversine/sphere",
        "result_cache": "disabled",
        "tie_breaker": "road_id",
    }
).to_frame("setting")

# %% [markdown]
# The preparation loop doubles the radius until every location has at least one
# candidate. Once a location has a road at distance `d <= radius`, every road
# outside the radius is farther than that candidate. Ranking inside the radius
# is therefore globally exact.

# %%
kinetica_sql = """
SELECT location_id, road_id, road_class, nearest_point, distance_m
FROM (
  SELECT /* KI_HINT_NO_QUERY_RESULT_CACHING */
         l.location_id, r.road_id, r.road_class,
         ST_CLOSESTPOINT(r.geometry, l.geometry, 1) AS nearest_point,
         ST_DISTANCE(r.geometry, l.geometry, 1) AS distance_m,
         ROW_NUMBER() OVER (
           PARTITION BY l.location_id
           ORDER BY ST_DISTANCE(r.geometry, l.geometry, 1), r.road_id
         ) AS nearest_rank
  FROM sedona_benchmark_locations l
  JOIN sedona_benchmark_roads r
    ON r.is_general_driving
   AND ST_DWITHIN(l.geometry, r.geometry, :radius_m, 1)
) ranked
WHERE nearest_rank = 1
"""
print(kinetica_sql)

# %% [markdown]
# The three solution arguments use Kinetica's spherical mode, matching
# SedonaDB geography distance. Replacing one with planar degrees or Vincenty
# spheroid distance would change candidate coverage and ranking.
#
# GPU telemetry must overlap the SQL interval. `nvidia-smi` visibility, a
# running container, or GPU memory allocated at startup does not prove this
# query used the GPU.

# %%
runs = sorted(Path("/benchmark-data/runs").glob("*-kinetica-*.json"))
if runs:
    import json
    import statistics

    latest = json.loads(runs[-1].read_text())
    measured = [
        item["elapsed_seconds"]
        for item in latest["measurements"]
        if item["kind"] == "measured"
    ]
    pd.Series(
        {
            "run_id": latest["run_id"],
            "tier": latest["road_tier"],
            "locations": latest["location_count"],
            "candidate_radius_distribution": latest[
                "candidate_radius_distribution"
            ],
            "measured_repetitions": latest["repetitions"],
            "median_warm_seconds": statistics.median(measured),
            "correctness": latest["correctness"]["passed"],
        }
    ).to_frame("value")
else:
    print("No Kinetica reference run is present yet; run load-kinetica and run-kinetica.")
