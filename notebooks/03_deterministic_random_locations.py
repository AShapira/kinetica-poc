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
# # 03 — Deterministic area-uniform locations
#
# **Learning objectives:** understand why longitude/latitude degree sampling is
# biased, reproduce a seeded generator, and validate containment and uniqueness.
#
# Rejection sampling occurs in EPSG:2039, where rectangular area is locally
# meaningful. Accepted points are transformed to EPSG:4326 for the engines.

# %%
import os
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd

data_root = Path(os.environ.get("BENCHMARK_DATA_DIR", "/benchmark-data"))
location_paths = sorted((data_root / "canonical").glob("israel_locations_*.parquet"))
if not location_paths:
    raise FileNotFoundError("Run ./benchmark.sh prepare locations first")
locations = gpd.read_parquet(location_paths[-1])
boundary = gpd.read_parquet(data_root / "canonical" / "israel_boundary.parquet")

# %%
checks = pd.Series(
    {
        "rows": len(locations),
        "unique_ids": locations.location_id.nunique(),
        "unique_points": locations.geometry.to_wkb().nunique(),
        "seed_values": locations.seed.nunique(),
        "crs_is_4326": locations.crs.to_epsg() == 4326,
        "all_contained": bool(
            locations.geometry.within(boundary.geometry.union_all()).all()
        ),
    }
)
checks.to_frame("value")

# %%
axis = boundary.plot(facecolor="#f4f0e8", edgecolor="#444", figsize=(7, 9))
locations.plot(ax=axis, markersize=1, alpha=0.45, color="#d1495b")
axis.set_title(
    f"Deterministic PCG64 locations "
    f"(n={len(locations):,}, seed={locations.seed.iloc[0]})"
)
axis.set_axis_off()
plt.show()

# %% [markdown]
# A repeated generation is identical only if boundary checksum, projection,
# PRNG family, seed, acceptance predicate, target count, and row ordering are
# all unchanged. The location manifest records those dependencies.
