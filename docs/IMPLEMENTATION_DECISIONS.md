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
