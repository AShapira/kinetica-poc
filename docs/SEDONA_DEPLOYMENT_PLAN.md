# Sedona deployment plan and implementation record

## Purpose and success criteria

The deployment is a local, single-user GIS laboratory. SedonaDB is the measured
single-node engine; SedonaSpark is the distributed learning environment. The
existing Kinetica installation remains independently managed.

Success requires pinned rootless-Podman services, loopback-only ports, no
committed credentials, reproducible Israel roads and locations, proved
GeoParquet bbox pruning, exact nearest-road results, multithreaded scaling
evidence, and executed explanatory notebooks linked to run manifests.

## Topology

```text
Overture 2026-07-22.0 (read only)
              |
              v
    SedonaDB preparation container
      | boundary | roads | locations
      v          v       v
     canonical GeoParquet on Linux ext4
        |                         |
        v                         v
 SedonaDB radius ranking       Kinetica load
 CPU scaling                   deployed GPU system
        \                         /
         correctness + result summaries
                     |
         notebooks, plots, documentation
```

The SedonaDB image uses Python 3.12. Direct dependencies are pinned in
`pyproject.toml`; the resolved environment and image digest become run
evidence. The official `apache/sedona:1.9.0` image supplies Spark 3.5.5 and its
one-master/one-worker Jupyter environment.

## Security boundary

- Containers use rootless Podman and `userns=keep-id`.
- Services publish only on `127.0.0.1`.
- Overture is mounted read-only.
- Containers drop all capabilities and set `no-new-privileges`.
- Kinetica credentials come from a mode-`0600` mounted file.
- Full data, persistence, spill, telemetry, and secrets are ignored.
- Kinetica changes are limited to tables prefixed `sedona_benchmark_`.

Jupyter has no application token because it is loopback-only. Do not expose it
on another interface without authentication and TLS.

## Resource model

SedonaDB uses a 12 GiB execution limit and dedicated spill directory. `taskset`
process affinity controls the measured independent variable because the
rootless user service does not delegate the cgroup `cpuset` controller.
Kinetica remains unrestricted.

| Label | CPUs | Interpretation |
|---|---|---|
| `physical_1` | `0` | one physical core |
| `physical_2` | `0,2` | two physical cores |
| `physical_4` | `0,2,4,6` | four physical cores |
| `physical_8` | even CPUs through 14 | eight physical cores |
| `physical_12` | even CPUs through 22 | twelve physical cores |
| `physical_14` | even CPUs through 26 | all physical cores |
| `logical_28` | `0-27` | all cores plus SMT siblings |

The runner records process affinity so an accidental resource mismatch is
visible in the run manifest.

## Lifecycle

```bash
./sedona.sh build
./sedona.sh start-db
./sedona.sh start-spark
./sedona.sh status
./sedona.sh smoke
./sedona.sh stop
```

SedonaDB Jupyter defaults to port 8889. SedonaSpark Jupyter defaults to 8890;
Spark master and worker interfaces are also loopback-only.

## Phases and gates

| Phase | Gate | Evidence |
|---|---|---|
| Runtime | images build; smoke query succeeds | image inventory and package lock |
| Boundary | one valid Overture IL land feature | boundary manifest |
| Roads | bbox pruning and clipping succeed | plan, class counts, road manifest |
| Locations | 10,000 unique contained points | location manifest and checksum |
| Sedona | every CPU/tier case completes | run manifests and plans |
| Kinetica | load and queries complete | radius proofs, plans, GPU telemetry |
| Correctness | exhaustive and cross-engine checks pass | correctness summary |
| Publication | notebooks and report rebuild | curated evidence and commits |

An invalid earlier phase cannot be converted into a later performance result.

## Rollback

Stopping Sedona removes only its containers. Generated files remain in
`BENCHMARK_DATA_DIR`. Kinetica rollback drops only the two benchmark tables;
the existing database and persistence are otherwise untouched. Git changes
remain on the local feature branch and are not pushed.
