# Notebook curriculum

The notebooks are ordered so each output becomes the next notebook's input.
Every `.py` file is a Jupytext percent-format source paired with an executed
`.ipynb`. Text sources make review meaningful; notebooks preserve compact
reference output.

| No. | Topic | Main output |
|---|---|---|
| 00 | environment and architecture | version/topology inventory |
| 01 | boundary and bbox pushdown | boundary inspection and scan plan |
| 02 | road classes and quality | tier statistics and road map |
| 03 | deterministic sampling | location distribution and validation |
| 04 | SedonaDB nearest road | exact radius join, plan, and result sample |
| 05 | SedonaSpark GIS | Spark/Sedona spatial operations |
| 06 | Kinetica equivalent | radius proof and result semantics |
| 07 | analysis and reproduction | final tables and scaling plots |

Notebooks read `BENCHMARK_DATA_DIR` and never embed private host paths.
Conceptual cells remain useful without full data, but reference execution
requires the canonical pipeline.
