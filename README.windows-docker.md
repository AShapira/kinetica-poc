# Windows Docker Desktop presentation benchmark

This optional workflow runs a small, local demonstration of the nearest-road
benchmark on a Windows workstation. It uses Docker Desktop, SedonaDB, and the
Kinetica Developer Edition GPU container.

It is **presentation-only**. Its 1,000-point Ashdod result is not a production
benchmark, a CI test, or comparable to the repository's Israel-wide publication
results. Docker Desktop and the workstation GPU are not the primary deployment
or validation environment.

## What the profile measures

- a 10 km projected buffer around the exact Overture Ashdod locality record;
- the buffer clipped to Israel's land division geometry;
- 1,000 deterministic points using the repository seed;
- the `general_driving` road tier only;
- one warm-up and three measured repetitions per engine;
- the same compact input-bundle hash for SedonaDB and Kinetica;
- exact cross-engine result validation; comparison output is refused on failure.

The generated presentation manifest records `comparable_to_publication: false`.
Results are written to a timestamped directory under `presentation-output` and
are never read by the production comparison command.

## Prerequisites

- A Windows clone of this repository, native Windows PowerShell, and Docker
  Desktop using Linux containers.
- Docker Desktop's WSL 2 backend with GPU support and a working NVIDIA Windows
  driver. The doctor command performs a real `nvidia-smi` container smoke test.
- At least 8 CPUs and 16 GB available to Docker for the default profile. If
  Docker Desktop uses WSL 2 resource mode, its ceiling may come from the Windows
  `.wslconfig` file rather than the Docker Desktop Advanced settings.
- The full pinned Overture release available in the primary WSL/RHEL benchmark
  environment, only for the one-time compact-bundle build.
- Acceptance of Kinetica's Developer Edition license terms. Kinetica is
  proprietary; its image, installer, credentials, and persistent data are not
  committed to this repository.

Docker Desktop's Windows GPU support is provided through its WSL 2 backend; it
does not apply to Windows containers. Kinetica's published production hardware
matrix should be consulted separately. This workflow does not claim that a
consumer workstation GPU is a certified Kinetica configuration.

## 1. Build the compact Ashdod bundle

Run this in the repository's primary WSL/RHEL environment. Use a destination
that does not yet exist. The builder reads the full Overture input read-only,
selects only the Ashdod and Israel division records plus bounding-box road
candidates, validates read-back counts, and writes per-file hashes.

```bash
./sedona.sh build
PRESENTATION_BUNDLE_DIR=/mnt/c/kinetica-presentation/ashdod-bundle \
  ./benchmark.sh build-presentation-bundle
```

The command requires the normal `OVERTURE_RELEASE_DIR` and
`BENCHMARK_DATA_DIR` settings, even though it writes only to
`PRESENTATION_BUNDLE_DIR`. It refuses to replace an existing destination.

Review the resulting `bundle-manifest.json`. The two GeoParquet files retain
the original Overture `id`, geometry, road class, subtype, and bounding-box
fields needed to reconstruct the canonical presentation inputs. Overture data
attribution remains recorded in the manifest.

## 2. Download and verify the Kinetica installer

In native Windows PowerShell, from the repository root:

```powershell
curl.exe https://files.kinetica.com/install/kinetica.bat -o kinetica.bat
Get-FileHash -Algorithm SHA256 .\kinetica.bat
```

The committed profile expects this SHA-256 value:

```text
8ba755caa75c96f1c8bf9b5b3d2137c8d5bb7742de46fb4b1410b20f348a842b
```

The installer is mutable upstream. If the checksum changes, do not simply
replace the configured hash. Review the new script and its `--help` output,
then make an intentional repository update. The installer itself is ignored by
Git.

## 3. Configure resources and paths

Copy the example to the ignored local configuration:

```powershell
Copy-Item .\presentation\windows-docker.example.json `
  .\presentation\windows-docker.local.json
notepad .\presentation\windows-docker.local.json
```

Set `paths.bundle` to the compact bundle from step 1. Relative paths are
resolved from the `presentation` directory. The default resource allocation is:

```json
{
  "docker": {
    "offline": true,
    "cpus": 8,
    "memory": "16g",
    "sedonaMemory": "12gb"
  }
}
```

`cpus` and `memory` become hard Docker limits for each benchmark client and the
Kinetica database container. `sedonaMemory` must remain below `memory`. Kinetica
also receives `--gpus all`. Docker Desktop must expose at least the configured
CPU and memory totals or the workflow stops in the doctor step.

With the example's `offline: true`, the workflow never builds or pulls an
image. It requires all three configured images to be loaded locally, applies
Docker's `--pull=never` policy to benchmark containers, and verifies the pinned
Kinetica digest. Set `offline` to `false` only on a connected staging machine
where `BuildImage` and registry pulls are intended.

The Kinetica ports bind to `127.0.0.1` only. The configurable defaults avoid the
usual ports except for the vendor installer's fixed host-manager port 9300:

| Service | Host port |
| --- | ---: |
| Database REST | 19191 |
| PostgreSQL wire | 15434 |
| Workbench | 18000 |
| GAdmin | 18080 |
| Reveal | 18088 |
| Host manager | 9300 |

Ensure port 9300 is not used by another Kinetica instance during installation.

## 4. Validate and run

Keep Docker Desktop running, then use native Windows PowerShell:

```powershell
.\presentation\windows-docker.ps1 -Action Doctor
.\presentation\windows-docker.ps1 -Action InstallKinetica
.\presentation\windows-docker.ps1 -Action Run
```

The default offline profile assumes the benchmark image was built on a
connected staging machine and transferred with the other images. On that
staging machine, temporarily set `offline` to `false` and run `BuildImage`.

For a new machine, the same sequence is available as:

```powershell
.\presentation\windows-docker.ps1 -Action All
```

In offline mode, `All` validates the local images, skips the build and all
pulls, installs Kinetica from its local pinned image, and runs the benchmark.

Kinetica's official installer prompts for the initial administrator password.
The benchmark later prompts for that password as a `SecureString`; it is sent
through standard input to a client container, written with mode 0600 on a tiny
container-only `tmpfs`, and removed when the container exits. It is never put
in the command line, configuration, image, or output directory.

The runner stops Kinetica before the Sedona measurement, starts it for the
Kinetica measurement, and restores an initially stopped Kinetica container on
exit. It will not remove or replace an existing container or persistence
directory.

## Output and interpretation

The final timestamped output includes:

- canonical boundary, roads, and 1,000 locations with manifests;
- raw result GeoParquet and run JSON for both engines;
- `evidence/presentation-summary.csv`;
- `evidence/correctness-summary.json`;
- SVG and PNG latency plots;
- `evidence/presentation-manifest.json` with bundle, Git, image, resource, and
  limitation metadata.

Treat the result as a presentation of the workflow, not as a hardware buying
guide or an engine-wide performance conclusion. The two engines have different
execution architectures, Docker CPU quotas are not physical-core pinning, and
the Kinetica client container runs alongside the database container.

## Air-gapped transfer contract

The runtime does not need internet access when `offline` is true. Transfer and
load these items through the environment's approved media process:

- this repository at the selected Git commit;
- the verified `kinetica.bat` installer;
- the compact Ashdod bundle and its `bundle-manifest.json`;
- `kinetica-poc-benchmark:windows-presentation`;
- `nvidia/cuda:13.0.1-base-ubuntu24.04`;
- the configured pinned Kinetica CUDA image.

For example, create an image archive on the staging workstation:

```powershell
docker save -o .\presentation-images.tar `
  kinetica-poc-benchmark:windows-presentation `
  nvidia/cuda:13.0.1-base-ubuntu24.04 `
  docker.io/kinetica/kinetica-cuda:7.2.3.18.20260707203019.ga
```

Then load it on the isolated workstation before running `Doctor`:

```powershell
docker load -i .\presentation-images.tar
```

The full Overture release is needed only where the compact bundle is built. The
Windows presentation runtime consumes the compact bundle and does not contact
Overture, a package index, a registry, or a cloud map service. Kinetica's
license terms still apply; stage any organization-required licensing material
before isolation.

## Troubleshooting

- If `Doctor` reports no GPU, confirm Docker Desktop is using Linux containers,
  WSL integration is enabled, and `docker run --rm --gpus all ... nvidia-smi`
  succeeds before retrying.
- If Docker exposes too little memory, increase the Docker/WSL 2 resource
  ceiling, restart Docker Desktop, and rerun `Doctor`.
- If Kinetica installation fails on a port, stop the conflicting local service.
  Do not use the vendor `clear`, `rm`, or `uninstall` commands unless you intend
  their destructive effect on that presentation instance.
- If a run is interrupted, keep its timestamped directory as diagnostic
  evidence and start a new run. The comparator selects the latest complete,
  correct run for each engine within one output directory.

## Upstream references

- [Kinetica Developer Edition](https://www.kinetica.com/kinetica-developer-edition/)
- [Docker Desktop GPU support](https://docs.docker.com/desktop/features/gpu/)
- [Docker Desktop WSL 2 backend](https://docs.docker.com/desktop/features/wsl/)
