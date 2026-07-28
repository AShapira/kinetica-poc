# Implementation decisions

This append-only log records material choices made while implementing the
approved Sedona GIS benchmark. Times use Asia/Jerusalem unless stated otherwise.

## 2026-07-28 — Restore the workspace sandbox

- **Phase:** repository scaffolding
- **Issue:** the required patch editor and ordinary commands failed because
  `bwrap` was absent; each fallback write would otherwise need to run outside
  the workspace sandbox.
- **Options:** continue with repeated privileged writes, stop implementation, or
  install RHEL's standard Bubblewrap package.
- **Choice:** installed `bubblewrap-0.10.0-3.el10` from the configured RHEL BaseOS
  repository with `sudo dnf install`.
- **Reason:** this restores the intended workspace isolation and allows all
  subsequent edits to use reviewable patches.
- **Impact:** no benchmark semantics changed. One small system package was added.
- **Validation:** `apply_patch` and sandboxed `rg` both succeeded afterward.

## 2026-07-28 — Use a custom SedonaDB image and official SedonaSpark image

- **Phase:** deployment
- **Issue:** SedonaDB and SedonaSpark have different runtime and learning needs.
- **Choice:** build a non-root Python 3.12 SedonaDB image and use
  `docker.io/apache/sedona:1.9.0` unchanged for Spark.
- **Reason:** the DB image can pin the complete benchmark environment while the
  Spark image preserves Apache's documented Spark 3.5.5/Jupyter topology.
- **Impact:** SedonaDB is the measured engine; Spark remains an educational
  environment and is not presented as a third benchmark result.
- **Validation:** image smoke queries and service health checks are acceptance
  gates.

## 2026-07-28 — Apply CPU affinity inside the rootless container

- **Phase:** performance harness
- **Issue:** the host user service does not delegate the cgroup v2 `cpuset`
  controller, so Podman's `--cpuset-cpus` fails before container startup.
- **Options:** reconfigure host cgroups, use rootful Podman, omit scaling, or
  constrain the benchmark process itself.
- **Choice:** start the benchmark with `taskset --cpu-list` inside the rootless
  container.
- **Reason:** this constrains the process and worker threads to the identical CPU
  sets without widening container privileges or changing host configuration.
- **Impact:** run manifests record the observed affinity, making the fallback
  auditable and preventing accidental unconstrained measurements.
- **Validation:** every requested affinity is checked in the run environment
  captured by the benchmark manifest.

## 2026-07-28 — Use an exact radius join for line nearest-neighbour queries

- **Phase:** correctness validation
- **Issue:** `ST_KNN` is attractive for point-to-point searches, but Apache
  Sedona documents centroid handling for non-point inputs. A road is a
  LineString, so centroid ranking does not prove the nearest point on the road.
  SedonaDB 0.4.0 also exposes `ST_ClosestPoint` for Geography but its advertised
  Geometry overload has no registered execution kernel in the published wheel.
- **Options:** accept centroid semantics, post-process candidates outside the
  timed query, or use an exact spatial range join.
- **Choice:** discover the smallest doubling radius that covers every location,
  execute a geography `ST_DWithin` join, rank candidates by geography
  `ST_Distance`, and calculate the result with geography `ST_ClosestPoint`.
- **Reason:** the nearest road must be among candidates once every location is
  covered; exact distance ranking preserves line semantics and uses the same
  algorithmic shape in SedonaDB and Kinetica.
- **Impact:** radius discovery is untimed and recorded. Each Sedona run is
  checked against an exhaustive all-roads ranking for a fixed 100-location
  prefix.
- **Validation:** the smoke and measured run manifests include radius and oracle
  results; cross-engine distances are checked separately.

## 2026-07-28 — Bucket candidate radii per location

- **Phase:** full 10,000-location capacity gate
- **Issue:** the first exact implementation used the largest discovered radius
  (16 km) for every point. One execution reached the configured 100 GB spill
  ceiling because a few remote points made dense urban locations generate
  unnecessarily broad candidate sets.
- **Options:** raise the spill ceiling, reduce the workload, abandon exact line
  semantics, or retain exactness with location-specific radii.
- **Choice:** assign each location to the first 1/2/4/8/16 km radius that has a
  candidate, union literal-radius spatial joins, then perform one exact distance
  ranking.
- **Reason:** a road outside a location's first covered radius cannot beat its
  in-radius minimum. This keeps the proof intact while bounding intermediate
  volume.
- **Impact:** the failed run produced no result manifest. Its 95 GB of
  regenerable files under the dedicated benchmark spill directory were deleted,
  restoring host free space from 152 GB to 250 GB. No canonical data or
  Kinetica persistence was touched.
- **Validation:** the 100-location smoke distribution was 44/22/12/17/5 across
  1/2/4/8/16 km, used under 0.6 GB RSS, created no retained spill, and matched
  the exhaustive oracle for all 100 points.

## 2026-07-28 — Run the exhaustive oracle once per semantic workload

- **Phase:** CPU scaling
- **Issue:** the exhaustive 100-point/all-road oracle is deliberately
  independent and takes much longer than the measured query at one core.
  Repeating it at every affinity does not test a semantic change.
- **Choice:** execute it for smoke and each full-resource tier. Scaling-only
  manifests explicitly mark the oracle as skipped and reference the identical
  full-resource semantic gate.
- **Impact:** measured queries, checksums, output materialization, and timing are
  unchanged. The completed one-core run retained its independently executed
  oracle; an in-progress two-core attempt was interrupted before producing a
  manifest and was rerun after this change.

## 2026-07-28 — Pin the Kinetica wheel's undeclared runtime dependency

- **Phase:** Kinetica load
- **Issue:** `gpudb==7.2.3.9` imports `typeguard.value` in its core module, but
  declares `typeguard` only for its optional `dataframe` extra. A default install
  therefore raises `ModuleNotFoundError` before authentication.
- **Choice:** pin `typeguard==4.4.4` directly in both the project dependencies
  and resolved lock file.
- **Reason:** an ephemeral clean-image probe confirmed that version exports
  `value` and allows the pinned `gpudb` module to import.
- **Impact:** no database state changed during the failed load; the error
  occurred before connection creation.

## 2026-07-28 — Verify Kinetica schema without the DB-API default option

- **Phase:** Kinetica load
- **Issue:** the pinned DB-API sends its `default_schema` connection value as an
  `execute/sql` parameter during `executemany`; Kinetica 7.2.3 rejects that
  parameter. Authentication and table creation had already succeeded.
- **Choice:** omit the incompatible client option, query `CURRENT_SCHEMA` on
  every connection, and abort unless it equals configured `ki_home`.
- **Reason:** a read-only probe confirmed the authenticated admin session
  already uses `ki_home`. Explicit verification preserves the schema contract
  without relying on the broken parameter path.
- **Impact:** the retry drops and recreates only the dedicated
  `sedona_benchmark_*` tables before loading.

## 2026-07-28 — Page every Kinetica DB-API result explicitly

- **Phase:** Kinetica correctness gate
- **Issue:** the smoke query returned 5,000 of 10,000 locations even though
  read-only database counts confirmed that all 383,603 roads and 10,000
  locations were loaded. The pinned client's `fetchall()` delegates to a single
  `fetchmany(cursor.arraysize)` call; its default array size is 5,000.
- **Choice:** consume every cursor in explicit 5,000-row pages, both during
  radius discovery and final result export, with an explicit `ORDER BY
  location_id` on each paged query. Without stable ordering, offset-based
  result pages can overlap while omitting other rows. The loader now also
  compares database counts with canonical input counts before reporting
  success.
- **Impact:** the incomplete smoke run is retained only as diagnostic evidence
  and is excluded from publication summaries. No measured publication run was
  accepted before this fix.

## 2026-07-28 — Use Podman's supported memory telemetry field

- **Phase:** Kinetica host telemetry
- **Issue:** the installed Podman release exposes `.MemPerc`, not `.Mem`, in
  custom stats templates. The GPU sampler continued, and the smoke query
  completed, but the container sampler wrote an error instead of measurements.
- **Choice:** record `.MemUsage` and `.MemPerc` alongside CPU, I/O, and PID
  fields. A direct no-stream probe against the running Kinetica container
  validated the corrected template before publication runs.

## 2026-07-28 — Match SedonaDB's spherical geography model in Kinetica

- **Phase:** cross-engine correctness
- **Issue:** the first complete comparison found thousands of distance deltas
  over one metre and a small number of different nearest roads. The query used
  Kinetica solution `2` (Vincenty/spheroid), while SedonaDB documents geography
  `ST_Distance` as a spherical approximation.
- **Choice:** use Kinetica solution `1` (Haversine/sphere) consistently in
  `ST_DWITHIN`, `ST_DISTANCE`, and `ST_CLOSESTPOINT`.
- **Reason:** a benchmark may compare implementations only after aligning the
  distance model. Mixing sphere and spheroid measures mathematical model
  choice, not engine correctness or performance.
- **Impact:** the completed solution-2 manifests remain diagnostic runs and are
  excluded by revision selection. All three Kinetica publication tiers are
  regenerated after this committed change.

## 2026-07-28 — Stop Kinetica during every Sedona measurement

- **Phase:** variance investigation and engine isolation
- **Issue:** the latest Sedona full-resource runs developed large warm-query
  tails. Topology and spill checks were clean, but the nominally idle Kinetica
  container still used roughly one-third of a CPU, 3.8 GB RAM, and 845
  processes.
- **Choice:** make a stopped Kinetica container an enforced Sedona precondition.
  The one-command reproduction path stops and restores it with an exit trap;
  individual commands fail with the exact lifecycle command to run.
- **Reason:** “no simultaneous benchmark query” is weaker than resource
  isolation on a single shared host. Background database work is an uncontrolled
  competitor.
- **Impact:** all Sedona scaling and full-resource tier cases are regenerated
  once under the enforced policy. Older manifests remain external diagnostic
  evidence; latest-case selection is deterministic and does not discard them.

## 2026-07-28 — Preserve the 28-thread Sedona memory-pressure result

- **Phase:** isolated rerun acceptance
- **Observation:** physical-core cases from one through twelve were stable; 14
  physical cores regressed. The 28-thread general-driving case rose from about
  2.2 seconds to 16.8 seconds across its five repetitions, with a 9.60-second
  median, 75% relative MAD, and 13.2 GiB peak client RSS. The service/rural
  28-thread median was 16.70 seconds. No spill files remained and all
  exhaustive correctness checks passed.
- **Choice:** publish these latest isolated measurements and mark relative MAD
  over 20% as high variability. Do not substitute the earlier faster
  full-thread run or drop late repetitions.
- **Interpretation:** on this build and host, adding SMT threads beyond the
  physical-core optimum creates memory/execution pressure and is not a valid
  “more CPUs must be faster” assumption. This is an observed configuration
  result, not a universal Sedona conclusion.
