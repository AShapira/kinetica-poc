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
# # 00 — Environment and architecture
#
# **Learning objectives**
#
# - distinguish SedonaDB from SedonaSpark;
# - identify every reproducibility dimension before computing a result;
# - understand which directories are source, regenerable state, and evidence.
#
# SedonaDB is a single-node Arrow/DataFusion spatial engine. SedonaSpark extends
# Spark for distributed spatial processing. The benchmark uses SedonaDB because
# it provides a native, multithreaded single-node spatial join comparable with
# the existing single-node Kinetica POC. Spark remains a learning environment.

# %%
import json
import os
import platform
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow
import shapely

versions = {
    "platform": platform.platform(),
    "python": platform.python_version(),
    "apache_sedona": version("apache-sedona"),
    "sedonadb": version("sedonadb"),
    "numpy": np.__version__,
    "pandas": pd.__version__,
    "pyarrow": pyarrow.__version__,
    "shapely": shapely.__version__,
}
pd.Series(versions, name="version").to_frame()

# %% [markdown]
# ## Data boundaries
#
# `OVERTURE_RELEASE_DIR` is read-only source. `BENCHMARK_DATA_DIR` is
# regenerable and may be large. `evidence/` is curated for Git. A credential is
# neither data nor configuration: it is mounted separately and is never shown.

# %%
paths = {
    "overture_configured": bool(os.environ.get("OVERTURE_RELEASE_DIR")),
    "benchmark_data_configured": bool(os.environ.get("BENCHMARK_DATA_DIR")),
    "repository": os.environ.get("BENCHMARK_REPO_DIR", str(Path.cwd())),
}
print(json.dumps(paths, indent=2))

# %% [markdown]
# ## Architecture checkpoint
#
# The fixed data flows to both engines. Engine output returns to one
# correctness gate before performance is interpreted. This prevents a fast but
# semantically different query from becoming a headline number.
#
# **Exercise:** identify which manifest fields should change if the same data is
# measured on a second host. CPU topology and image digest may change; source,
# configuration, and canonical checksums should not.
