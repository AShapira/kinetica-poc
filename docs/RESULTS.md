# Benchmark results

## Scope and acceptance

These results use Overture `2026-07-22.0`, its exact `IL` land-country
geometry, 383,603 clipped road fragments, and 10,000 deterministic PCG64
locations (seed `20260727`). Every publication row has one excluded warm-up
and five measured materialized executions. Preparation, radius discovery,
cold execution, and exhaustive oracles are outside the reported warm latency.

The machine-readable sources are:

- [`benchmark-summary.csv`](../evidence/benchmark-summary.csv)
- [`correctness-summary.json`](../evidence/correctness-summary.json)
- [`publication-manifest.json`](../evidence/publication-manifest.json)
- [`engine-comparison.png`](../evidence/plots/engine-comparison.png)
- [`sedonadb-scaling.png`](../evidence/plots/sedonadb-scaling.png)

## Which road types are worth including?

Primary alone is too narrow for a nearest-road benchmark. The canonical data
contains only 8,873 primary fragments, while a random home or business is
usually closest to a residential, tertiary, or unclassified street. A
primary-only test answers “how far is this point from a major primary road?”,
not “what is the nearest drivable road?”

| Tier | Included classes | Fragments | Intended question |
|---|---|---:|---|
| arterial | motorway, trunk, primary, secondary | 45,542 | nearest major through-road |
| general driving | arterial + tertiary, unclassified, residential, living street | 264,160 | nearest ordinary drivable road |
| service/rural | general + service, track | 383,603 | sensitivity to broad access and rural coverage |

`general_driving` is therefore the headline workload. `arterial` is useful for
access-to-major-road analysis. `service_rural` is valuable as a density and
semantics sensitivity case, but service roads and tracks should not silently
define the normal-road headline because access and surface quality vary.
Footways, paths, steps, pedestrian ways, cycleways, bridleways, raceways, and
unknown classes are excluded.

## Full-resource deployed-system comparison

This table compares the deployed configurations, not hardware-neutral engine
efficiency. SedonaDB has all 28 logical CPUs available. Kinetica is
unrestricted and uses the deployed RTX 5080 GPU system. Lower latency is
better.

| Engine | Road tier | Median warm (s) | p95 (s) | Throughput (locations/s) | Relative MAD | Note |
|---|---|---:|---:|---:|---:|---|
| SedonaDB | arterial | 0.180 | 0.182 | 55,581 | 1.1% | stable |
| Kinetica | arterial | 0.667 | 0.715 | 14,991 | 8.0% | stable |
| SedonaDB | general driving | 9.596 | 16.249 | 1,042 | 75.2% | **high variability / SMT pressure** |
| Kinetica | general driving | 0.958 | 0.967 | 10,437 | 1.0% | stable |
| SedonaDB | service/rural | 16.697 | 20.555 | 599 | 11.1% | memory-sensitive |
| Kinetica | service/rural | 0.996 | 1.025 | 10,044 | 2.2% | stable |

At the tested full-resource settings, SedonaDB is 3.71× faster for arterial
roads, while Kinetica is 10.0× faster for general driving and 16.8× faster for
service/rural. These ratios are facts about these configurations on this host.
They are not vendor-general conclusions.

The full-thread Sedona headline is not its best tested setting. At 12 physical
cores, SedonaDB's general-driving median is 0.704 s, 1.36× faster than the
0.958 s unrestricted Kinetica median. The benchmark therefore demonstrates why
resource scaling must accompany a single headline number.

## SedonaDB multithreaded scaling

The physical series uses one hardware thread from each core. The 28-CPU point
adds all SMT siblings and is intentionally shown separately.

| CPU mode | CPUs | Median (s) | Speedup | Parallel efficiency | Relative MAD |
|---|---:|---:|---:|---:|---:|
| physical | 1 | 5.282 | 1.00× | 100.0% | 0.3% |
| physical | 2 | 2.196 | 2.41× | 120.3% | 0.5% |
| physical | 4 | 1.187 | 4.45× | 111.3% | 1.3% |
| physical | 8 | 0.706 | 7.48× | 93.5% | 3.8% |
| physical | 12 | 0.704 | 7.50× | 62.5% | 3.1% |
| physical | 14 | 0.950 | 5.56× | 39.7% | 5.1% |
| SMT | 28 | 9.596 | 0.55× | 2.0% | **75.2%** |

The superlinear two- and four-core points are plausible cache/parallel
execution effects on this fixed workload, not a claim of unbounded scaling.
The useful knee is eight to twelve physical cores. Fourteen cores regress, and
SMT is actively harmful.

The isolated 28-thread repetitions were 2.145, 2.223, 9.596, 13.995, and
16.812 seconds while peak client RSS reached 13.2 GiB. Kinetica was stopped,
no Sedona spill remained, and correctness still passed. The late-run slowdown
is preserved and flagged rather than removed as an outlier. It indicates
memory/execution pressure in this SedonaDB build and host configuration.

## Kinetica GPU and container telemetry

Host telemetry was sampled every 0.5 seconds for the complete Kinetica
benchmark process. The raw `nvidia-smi` and Podman samples are committed under
[`evidence/telemetry`](../evidence/telemetry/).

| Tier | Peak GPU utilization | Mean sampled utilization | Peak GPU memory |
|---|---:|---:|---:|
| arterial | 39% | 14.7% | 3,972 MiB |
| general driving | 14% | 3.9% | 3,972 MiB |
| service/rural | 68% | 7.9% | 3,972 MiB |

Non-zero utilization establishes GPU activity during the harness interval.
The mean includes radius preparation and inter-query gaps, so it is not a
kernel-only utilization metric.

## Correctness

Correctness passed for all 30,000 cross-engine tier/location pairs at a
one-metre acceptance tolerance:

- zero distance mismatches; maximum delta 6.3 mm;
- zero nearest-point mismatches; maximum point delta 34.1 cm;
- every closest point within 2 mm of its reported road in EPSG:2039;
- four different road IDs, all explained by equivalent overlapping/tied
  geometry; zero unexplained IDs; and
- 100 deterministic locations per tier matched SedonaDB's exhaustive all-road
  oracle exactly.

The sphere model is aligned: SedonaDB geography uses spherical distance and
Kinetica uses solution `1` for `ST_DWITHIN`, `ST_DISTANCE`, and
`ST_CLOSESTPOINT`. Earlier spheroid diagnostics are excluded from publication.

## GeoParquet bbox pruning and datasets

The global transportation scan applies all four scalar GeoParquet bbox overlap
predicates before exact geometry intersection:

```text
xmin <= IL.xmax AND xmax >= IL.xmin
ymin <= IL.ymax AND ymax >= IL.ymin
```

[`bbox-road-scan.txt`](../evidence/plans/bbox-road-scan.txt) records these as
Parquet partial filters. Exact `ST_Intersects` and `ST_Intersection` then clip
candidate lines to the Overture Israel boundary. Scalar bboxes are only a
pruning mechanism, never the final inclusion test.

| Canonical dataset | Rows | Size | SHA-256 prefix |
|---|---:|---:|---|
| Israel boundary | 1 | 138,602 B | `69b8c6bcc6a3` |
| clipped Israel roads | 383,603 | 69,377,878 B | `14bcd2b36513` |
| deterministic locations GeoParquet | 10,000 | 727,624 B | `4bcaa285292d` |
| deterministic locations CSV | 10,000 | 736,698 B | `957fbe51c5c8` |

Full files stay under the external benchmark data root. Committed manifests,
100-row GeoParquet samples, plans, selected run logs, telemetry, summaries,
plots, and notebooks are sufficient to audit lineage without committing the
69 MB road dataset or row-level result exports.

## Interpretation boundary

- Do not combine preparation, cold, warm-up, measured query, or oracle time.
- Do not call the Kinetica/Sedona comparison hardware-neutral.
- Do not infer performance beyond this WSL2/RHEL host, engine build, data
  release, and resource policy.
- Do not infer that more logical CPUs improve SedonaDB; this run shows the
  opposite after the physical-core knee.
- Do not report an invalid or model-mismatched result because it looks faster.
