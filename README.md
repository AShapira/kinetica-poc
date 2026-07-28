# Sedona GIS laboratory and Kinetica benchmark

This repository now provides a reproducible GIS laboratory for learning
SedonaDB and SedonaSpark and comparing an exact nearest-road workflow with the
existing Kinetica GPU POC.

The benchmark extracts an Israel road network from Overture Maps GeoParquet,
generates 10,000 deterministic locations inside the land boundary, and finds
the nearest road and closest point for every location. It measures SedonaDB
across physical-core and SMT CPU sets and compares the full-resource deployed
systems. This is explicitly a CPU-versus-configured-GPU comparison, not a
claim of equal hardware.

## Start here

1. Read the [Sedona deployment plan](docs/SEDONA_DEPLOYMENT_PLAN.md).
2. Copy `.env.example` to `.env` and set the two data paths.
3. Run `./sedona.sh build` and `./sedona.sh smoke`.
4. Run `./benchmark.sh smoke`.
5. Follow the [notebook curriculum](notebooks/README.md) or the
   [full reproduction guide](docs/REPRODUCIBILITY.md).

## Reproducibility contract

- Overture release `2026-07-22.0`
- SedonaDB 0.4.0 through `apache-sedona[db]==1.9.0`
- SedonaSpark 1.9.0 with Spark 3.5.5 in rootless local multithreaded mode
- NumPy PCG64 seed `20260727`
- `general_driving` headline road tier
- one warm-up and five measured repetitions
- 1 metre correctness tolerance plus exhaustive sample validation

Full source and generated GeoParquet stay outside Git. Committed manifests,
schemas, samples, plans, notebooks, plots, and summaries make omitted
artifacts inspectable and rebuildable.

## Sedona documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Datasets and lineage](docs/DATASETS.md)
- [Benchmark methodology](docs/BENCHMARK_METHODOLOGY.md)
- [Reproduction](docs/REPRODUCIBILITY.md)
- [Results](docs/RESULTS.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Implementation decisions](docs/IMPLEMENTATION_DECISIONS.md)

## Existing Kinetica POC

This repository documents a local, single-node proof of concept for learning
Kinetica's GPU-accelerated database on rootless Podman inside a RHEL WSL2
distribution.

The tested environment used:

- RHEL 10.2 under WSL2
- Podman 5.8
- NVIDIA Container Toolkit with CDI
- an NVIDIA GeForce RTX 5080
- Kinetica Developer Edition 7.2.3

This is an **unsupported evaluation topology**, not a production deployment.
Kinetica's published matrix does not list Podman, WSL2, RHEL 10, or the RTX
5080 as certified combinations.

## Repository contents

- `DEPLOYMENT_PLAN.md` — requirements, risks, and staged deployment plan
- `KINETICA_ARTIFACTS.md` — pinned installer and image provenance
- `PHASE_2_VALIDATION.md` — Podman/NVIDIA runtime validation
- `PHASE_3_VALIDATION.md` — installation and stability evidence

## Proprietary software notice

Kinetica is proprietary software. This repository does not contain Kinetica
binaries, container images, license keys, or its installer. Download Kinetica
only from its official site and review the
[Kinetica Developer License Agreement](https://www.kinetica.com/wp-content/uploads/2024/11/Kinetica-Developer-License-Agreement-Dec-10-2021.pdf)
before use.

The local POC required a small, undistributed compatibility adjustment to the
vendor installer because Podman repeats values in `inspect` output. This
repository records the behavior and outcome without redistributing vendor code.

## Lifecycle commands

After local installation, manage Kinetica with its vendor script:

```bash
DOCKER_PROGRAM=podman ./kinetica start
DOCKER_PROGRAM=podman ./kinetica status
DOCKER_PROGRAM=podman ./kinetica stop
DOCKER_PROGRAM=podman ./kinetica restart
```

Persist database data on the WSL ext4 filesystem rather than under `/mnt/c`.
Do not use the installer's `clear` command unless permanent deletion of the POC
data is intended.
