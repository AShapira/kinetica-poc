# Reproduction guide

## Prerequisites

- RHEL 10 or a compatible Linux environment
- rootless Podman 5 or later
- 28 logical CPUs for the reference CPU-set matrix
- at least 16 GiB available RAM and Linux-ext4 spill/output storage
- verified local Overture release `2026-07-22.0`
- existing Kinetica 7.2.3 only for the cross-engine phase

The full global source is about 610 GB, of which transportation is about 90 GB.
The benchmark does not download it. Verify the release independently before
running.

## Local configuration

```bash
cp .env.example .env
chmod 600 .env
```

Set:

```text
OVERTURE_RELEASE_DIR=/linux/path/to/release/2026-07-22.0
BENCHMARK_DATA_DIR=/linux/path/to/sedona-benchmark
KINETICA_USER=admin
KINETICA_PASSWORD_FILE=/linux/private/path/kinetica-admin-password
```

The password file must be mode `0600`. `.env` and the password are ignored.

## Build and validate

```bash
./sedona.sh build
./sedona.sh smoke
./sedona.sh start-db
./sedona.sh start-spark
./sedona.sh status
```

The DB smoke test runs a spatial SQL expression. For Spark, open the loopback
Jupyter endpoint and execute notebook 05 after the canonical data exists.

## Fast validation

```bash
./benchmark.sh smoke
```

Smoke mode uses 100 locations and two warm measurements. It validates code and
schemas but is not benchmark evidence.

## Full preparation

```bash
./benchmark.sh prepare boundary
./benchmark.sh prepare roads
./benchmark.sh prepare locations
```

Or:

```bash
./benchmark.sh prepare
```

Inspect `BENCHMARK_DATA_DIR/manifests` after each stage. Stop if any manifest
has `validation.passed=false`.

## Full benchmark

Run phases individually for diagnosis:

```bash
DOCKER_PROGRAM=podman ./kinetica stop
./benchmark.sh run-scaling general_driving
SEDONA_CPUSET=0-27 ./benchmark.sh run-sedona --tier arterial
SEDONA_CPUSET=0-27 ./benchmark.sh run-sedona --tier service_rural
DOCKER_PROGRAM=podman ./kinetica start
./benchmark.sh load-kinetica
./benchmark.sh run-kinetica --tier arterial
./benchmark.sh run-kinetica --tier general_driving
./benchmark.sh run-kinetica --tier service_rural
./benchmark.sh compare
```

`./benchmark.sh reproduce` runs the sequence as one operation. The individual
form is safer when diagnosing a first run because completed stages remain
usable. The single-command path records whether Kinetica was running, stops it
for all Sedona measurements, and restores it before the Kinetica phase (also on
an early exit). Individual Sedona commands fail fast if Kinetica is still
running. A merely idle Kinetica container is not accepted as isolation because
its background processes still consume host CPU and memory.

## Notebooks

Jupytext pairs each `.py` source with an `.ipynb`:

```bash
podman run --rm --userns=keep-id \
  -v "$PWD:/workspace:Z" \
  localhost/sedona-kinetica-benchmark:0.1.0 \
  jupytext --sync notebooks/*.py
```

Execute with `jupyter nbconvert --execute --inplace`. The notebooks detect
missing full data and explain prerequisites rather than silently fabricating
measurements.

## Evidence verification

Before accepting results:

1. validate JSON against `schemas/`;
2. compare source/config/canonical hashes across runs;
3. confirm expected affinity in every run;
4. inspect the bbox plan for scan predicates;
5. check correctness summary has non-zero checked rows and zero mismatches;
6. verify all planned CPU/tier cases exist;
7. execute all notebooks cleanly;
8. inspect plots, titles, units, and hardware labels.

## Re-running on another host

Override CPU sets in `benchmark.yaml` and record the new topology. Keep source,
canonical data, configuration, and engine versions unchanged if the purpose is
host comparison. Never merge results from different hosts into one scaling
line without a visible host dimension.

## Recovery

Preparation is stage-oriented. Valid canonical files can be reused after a
failed query. A failed intermediate output should be removed only after its
manifest and error are preserved. Do not delete Overture source or Kinetica
persistence.
