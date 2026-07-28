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
# # 02 — Road classes, clipping, and quality
#
# Primary roads alone omit secondary, local, residential, and rural access.
# This notebook studies three cumulative networks rather than selecting one
# arbitrary class.

# %%
import os
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd

data_root = Path(os.environ.get("BENCHMARK_DATA_DIR", "/benchmark-data"))
roads_path = data_root / "canonical" / "israel_roads.parquet"
if not roads_path.exists():
    raise FileNotFoundError("Run ./benchmark.sh prepare roads first")
roads = gpd.read_parquet(roads_path)
roads.head(3)

# %%
quality = pd.Series(
    {
        "rows": len(roads),
        "unique_road_ids": roads.road_id.nunique(),
        "valid": int(roads.geometry.is_valid.sum()),
        "empty": int(roads.geometry.is_empty.sum()),
        "total_length_km": roads.length_m.sum() / 1000,
    }
)
quality.to_frame("value")

# %%
class_counts = roads.groupby("road_class").agg(
    fragments=("road_id", "size"), length_km=("length_m", lambda values: values.sum() / 1000)
)
class_counts.sort_values("fragments", ascending=False)

# %% [markdown]
# ## Cumulative tier semantics
#
# - arterial: motorway through secondary;
# - general driving: arterial plus tertiary, unclassified, residential, and
#   living street;
# - service/rural: general driving plus service and track.
#
# Because the tiers are cumulative, widening a tier cannot reduce its road
# count. That invariant is tested before benchmarking.

# %%
tier_counts = pd.Series(
    {
        "arterial": int(roads.is_arterial.sum()),
        "general_driving": int(roads.is_general_driving.sum()),
        "service_rural": int(roads.is_service_rural.sum()),
    }
)
assert tier_counts.is_monotonic_increasing
tier_counts.to_frame("fragments")

# %%
sample = roads[roads.is_general_driving].sample(
    min(5000, int(roads.is_general_driving.sum())), random_state=7
)
axis = sample.plot(column="road_class", linewidth=0.35, figsize=(7, 9), legend=True)
axis.set_title("General-driving road sample; clipped to the study boundary")
axis.set_axis_off()
plt.show()

# %% [markdown]
# **Caveat:** fragments are centerlines, not driveable polygons or a routing
# graph. Nearest-road distance answers a proximity question; it does not prove
# legal or connected vehicle access.
