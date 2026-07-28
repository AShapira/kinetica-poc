# Benchmark methodology

## Question and hypotheses

The benchmark asks how long the deployed systems take to map every query point
to the closest point on an included Israel road. It also asks how SedonaDB
scales with CPU parallelism.

The experiment does not ask which vendor is universally faster. Kinetica is a
configured GPU database; SedonaDB's measured range-join path is CPU. Hardware,
algorithms, ingestion models, and caching are disclosed rather than disguised.

Expected observations:

1. broader road tiers increase preparation and object-side work;
2. SedonaDB improves as physical cores are added, then may show lower marginal
   benefit at the SMT endpoint; and
3. preparation, cold latency, and warm latency describe different operational
   questions and must not be combined.

## Fixed workload

- Overture release `2026-07-22.0`
- Overture-defined IL country land geometry
- 10,000 unique PCG64 points, seed `20260727`
- canonical clipped road fragments
- exact spherical distance in metres
- deterministic road-ID tie breaker

The headline `general_driving` tier includes motorway, trunk, primary,
secondary, tertiary, unclassified, residential, and living-street roads. The
arterial and service/rural tiers measure sensitivity to network density.

## GeoParquet filtering

The global segment scan projects only needed fields and applies scalar bbox
overlap before exact geometry work. A saved execution plan must demonstrate
that the predicates reach the Parquet scan. Acceptance also compares total
input row groups/bytes with those read by the query.

Bbox overlap is not treated as Israel membership. Every candidate receives an
exact intersection and is clipped to the boundary.

## SedonaDB algorithm

The selected tier is materialized before the spatial join. The benchmark does
not use `ST_KNN` for roads: Apache Sedona documents centroid behavior when a
KNN input is not a point, which answers a different question for LineStrings.
Instead SedonaDB uses the same proved-radius structure as Kinetica:

```sql
WITH ranked AS (
  SELECT l.location_id, r.road_id,
         ST_ToGeometry(
           ST_ClosestPoint(r.geography, l.geography)
         ) AS nearest_point,
         ST_Distance(r.geography, l.geography) AS distance_m,
         ROW_NUMBER() OVER (
           PARTITION BY l.location_id
           ORDER BY ST_Distance(r.geography, l.geography), r.road_id
         ) AS nearest_rank
  FROM locations l
  JOIN roads r ON ST_DWithin(l.geography, r.geography, :proved_radius_m)
)
SELECT * FROM ranked WHERE nearest_rank = 1;
```

Starting at 1 km, untimed coverage queries assign each still-uncovered location
to the first doubling radius that contains a road. The timed query is a union
of literal-radius spatial joins (1, 2, 4, 8, 16 km as needed), followed by one
global ranking. Any road outside a location's assigned radius is farther than
its in-radius nearest candidate. Per-location buckets prevent a few remote
points from forcing every dense urban point into the maximum-radius join.
Geography operations return spherical metres and the road ID makes exact ties
deterministic.

The timed operation fully materializes all output and calculates a deterministic
checksum. Interactive display or partial retrieval is never timed.

## Kinetica algorithm

Kinetica uses the equivalent proved-radius algorithm:

1. start at 1 km and assign covered locations to that radius;
2. double only for still-uncovered locations;
3. union literal-radius joins with spherical `ST_DWITHIN`;
4. rank candidates by spherical `ST_DISTANCE`, then road ID;
5. calculate `ST_CLOSESTPOINT` for rank one.

SedonaDB geography distance is a spherical approximation. Kinetica therefore
uses solution `1` (Haversine/sphere) for all three spatial functions; solution
`2` is Vincenty/spheroid and is intentionally not mixed into this equivalence
test. See the official [SedonaDB `ST_Distance` reference][sedona-distance] and
[Kinetica spatial expression reference][kinetica-expressions].

[sedona-distance]: https://sedona.apache.org/sedonadb/latest/reference/sql/st_distance/
[kinetica-expressions]: https://docs.kinetica.com/7.2/concepts/expressions/

Radius discovery is preparation. Once each location has an in-radius
candidate, no feature outside its assigned radius can be closer than the
minimum candidate. The report includes the full radius distribution and
coverage proof.

Kinetica result caching is disabled. Plan and data warming remain representative
of a warm analytical service.

## Timing protocol

For each engine/tier/resource case:

1. verify inputs, affinity, service state, and memory;
2. record an untimed plan;
3. measure one cold execution;
4. run one excluded warm-up;
5. measure five warm executions;
6. export one complete result for correctness;
7. run the exhaustive oracle for the full-resource reference (scaling-only
   cases record the reference instead of repeating it);
8. record environment, telemetry, checksum, and status.

The headline is median warm latency. Also report p95, min, max, median absolute
deviation, throughput, speedup, and parallel efficiency. Five repetitions show
run variability but do not justify claims of broad statistical significance.
Rows with `MAD / median > 0.20` are retained and visibly flagged as high
variability; they are not silently replaced or averaged with a different
resource case.

Competing benchmark engines do not run simultaneously. The Kinetica container
is stopped during Sedona measurements and restored before its own phase;
leaving it idle is insufficient because database background work still consumes
CPU and memory. Kinetica remains unrestricted during its measurements.
SedonaDB uses six physical-core points and one 28-thread SMT endpoint.

## Correctness

Performance is publishable only when:

- every tier/engine returns exactly one row for each of 10,000 IDs;
- IDs are unique and result values are non-null;
- the closest point lies on the reported road;
- cross-engine distances and points agree within 1 metre;
- different IDs are explained by tied or overlapping geometry; and
- at least 100 deterministic locations per full-resource tier pass exhaustive
  all-road ranking; CPU-affinity scaling cases reuse that semantic gate because
  affinity does not alter the data or query.

All mismatches are retained with both results, delta, source road IDs, and
geometry. An unexplained mismatch blocks the final comparison.

## Telemetry

Run evidence records process CPU and RSS, cgroup affinity, container I/O and
memory, spill, execution plans, Kinetica GPU utilization and memory, engine
versions, image digests, and host metadata. GPU visibility alone is not
evidence of GPU query work; utilization must overlap the query interval.

## Interpretation rules

- Do not combine preparation and query time.
- Do not label the Kinetica/Sedona comparison hardware-neutral.
- Do not infer scalability beyond the tested host.
- Do not compare rows with different canonical checksums.
- Do not report a faster invalid result.
- Preserve surprising observations; explain or label them instead of removing
  them as outliers.
