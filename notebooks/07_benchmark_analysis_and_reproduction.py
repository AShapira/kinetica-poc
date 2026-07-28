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
# # 07 — Benchmark analysis and reproduction
#
# **Learning objectives:** validate result completeness before plotting, compute
# speedup and efficiency, and interpret heterogeneous hardware honestly.
#
# This notebook reads committed summaries. It does not recompute values from
# screenshots or copy numbers from prose.

# %%
import json
import os
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from IPython.display import display

repo_root = Path(
    os.environ.get(
        "BENCHMARK_REPO_DIR",
        Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd(),
    )
)
summary_path = repo_root / "evidence" / "benchmark-summary.csv"
if not summary_path.exists():
    raise FileNotFoundError("Run ./benchmark.sh compare first")
summary = pd.read_csv(summary_path)
display(summary)

# %%
assert summary.correctness_passed.all(), "Invalid runs must not be analysed"
required = {"arterial", "general_driving", "service_rural"}
assert required.issubset(set(summary.road_tier))
correctness = json.loads(
    (repo_root / "evidence" / "correctness-summary.json").read_text(
        encoding="utf-8"
    )
)
assert correctness["passed"] and correctness["checked_rows"] == 30_000
print("Road tiers present:", sorted(set(summary.road_tier)))
print("Engines present:", sorted(set(summary.engine)))
print("Cross-engine rows checked:", correctness["checked_rows"])

# %% [markdown]
# ## Scaling
#
# Speedup is `T1 / Tp`. Parallel efficiency is speedup divided by the number of
# physical cores for the physical-core series. The 28-thread SMT point is shown
# separately because it is not 28 independent physical cores.

# %%
scaling = summary[
    (summary.engine == "sedonadb") & (summary.road_tier == "general_driving")
].sort_values("cpu_count")
if not scaling.empty:
    scaling = scaling.assign(
        throughput_gain=scaling.throughput_locations_s
        / scaling.iloc[0].throughput_locations_s,
    )
display(
    scaling[
        [
            "cpu_count",
            "median_seconds",
            "p95_seconds",
            "speedup",
            "parallel_efficiency",
            "relative_mad",
            "high_variability",
        ]
    ]
)

# %%
axis = scaling.plot(
    x="cpu_count",
    y="median_seconds",
    marker="o",
    legend=False,
    figsize=(8, 5),
)
axis.set_title("SedonaDB general-driving nearest-road scaling")
axis.set_xlabel("Effective logical CPUs")
axis.set_ylabel("Median warm latency (seconds)")
axis.grid(True, alpha=0.3)
plt.show()

# %% [markdown]
# ## Cross-engine interpretation
#
# The full-resource comparison answers: “How did these two deployed systems
# perform on this fixed canonical workload?” It does not isolate software from
# hardware. Kinetica GPU telemetry and Sedona CPU affinity must remain beside
# the latency table.

# %%
headline = summary[summary.road_tier == "general_driving"][
    [
        "engine",
        "cpu_count",
        "location_count",
        "median_seconds",
        "p95_seconds",
        "throughput_locations_s",
    ]
]
display(headline)

# %%
best_physical = scaling[scaling.cpu_count <= 14].sort_values(
    "median_seconds"
).iloc[0]
kinetica = headline[headline.engine == "kinetica"].iloc[0]
print(
    "Best Sedona physical-core median:",
    f"{best_physical.median_seconds:.3f}s on {best_physical.cpu_count} CPUs",
)
print(
    "Kinetica unrestricted median:",
    f"{kinetica.median_seconds:.3f}s",
)
print(
    "Sedona advantage at its best physical setting:",
    f"{kinetica.median_seconds / best_physical.median_seconds:.2f}x",
)

# %% [markdown]
# The 28-thread Sedona row remains in the table even when flagged. Its high
# relative MAD is a result to explain, not an outlier to delete. This distinction
# also prevents the best physical-core number from being mislabeled as the
# deployed full-resource comparison.

# %% [markdown]
# **Final checks**
#
# - all expected cases and five measured repetitions exist;
# - canonical checksums match;
# - correctness reports zero unexplained mismatches;
# - preparation and cold time are not in the warm headline;
# - plot title, axes, units, and hardware labels describe the actual metric.
