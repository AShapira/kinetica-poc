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
# # 01 — Israel boundary and GeoParquet bbox pushdown
#
# **Learning objectives:** inspect the exact boundary definition, understand a
# rectangle-overlap predicate, and separate candidate pruning from exact
# geometry tests.
#
# The study uses the Overture feature satisfying `country='IL'`,
# `subtype='country'`, and `is_land=true`. This is an explicit source-data
# definition, not a claim about another boundary source.

# %%
import os
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt

data_root = Path(os.environ.get("BENCHMARK_DATA_DIR", "/benchmark-data"))
boundary_path = data_root / "canonical" / "israel_boundary.parquet"
if not boundary_path.exists():
    raise FileNotFoundError("Run ./benchmark.sh prepare boundary first")
boundary = gpd.read_parquet(boundary_path)
boundary[["division_id", "country", "subtype", "is_land"]]

# %%
print("CRS:", boundary.crs)
print("Bounds [xmin, ymin, xmax, ymax]:", boundary.total_bounds.tolist())
print("Valid:", bool(boundary.geometry.is_valid.all()))
axis = boundary.plot(facecolor="#d8e8f3", edgecolor="#175676", figsize=(6, 8))
axis.set_title("Overture-defined IL land boundary")
axis.set_axis_off()
plt.show()

# %% [markdown]
# ## Why four comparisons?
#
# Two rectangles overlap unless one is entirely left, right, above, or below
# the other. The positive overlap form is:
#
# ```text
# road.xmin <= israel.xmax
# road.xmax >= israel.xmin
# road.ymin <= israel.ymax
# road.ymax >= israel.ymin
# ```
#
# This retains long roads crossing the rectangle even when neither endpoint is
# inside it. The exact `ST_Intersects` and clipping steps still decide Israel
# membership.

# %%
plan = Path("evidence/plans/bbox-road-scan.txt")
if plan.exists():
    print(plan.read_text()[:5000])
else:
    print("The scan plan is created by the road preparation stage.")

# %% [markdown]
# **Validation question:** does the plan show the four nested bbox predicates at
# the Parquet scan? If not, the output may still be correct, but the required
# pruning performance claim has not been demonstrated.
