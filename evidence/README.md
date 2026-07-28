# Curated benchmark evidence

This directory is the small, commit-safe evidence package for the benchmark
reported in [`docs/RESULTS.md`](../docs/RESULTS.md).

| Path | Contents |
|---|---|
| `datasets/` | full canonical indexes and manifests with hashes/statistics |
| `samples/` | 100-row GeoParquet samples for roads and locations |
| `plans/` | GeoParquet bbox scan and engine query plans |
| `runs/` | the 12 selected five-repetition run manifests |
| `telemetry/` | raw GPU and container samples for selected Kinetica runs |
| `plots/` | reproducible PNG and SVG output |
| `benchmark-summary.csv` | latency, dispersion, throughput, scaling, resources |
| `correctness-summary.json` | 30,000-row cross-engine and oracle validation |
| `publication-manifest.json` | dataset/run/artifact lineage and SHA-256 hashes |

Full canonical data, result rows, spill, and unselected diagnostic runs remain
under `BENCHMARK_DATA_DIR`. The publication selector is deterministic: latest
clean, correct, 10,000-location, five-repetition run for each engine, tier, and
exact CPU affinity. Smoke and failed diagnostic manifests cannot enter the
summary.

The absolute paths inside copied run manifests describe the measured external
environment. Their corresponding curated copies are listed here; the
publication manifest stores their run IDs, revisions, image digests, CPU
affinities, and result checksums.
