# Kinetica GPU Developer Edition on Podman, RHEL WSL

Assessment date: 2026-07-19

## Execution status

| Phase | Status | Notes |
|---|---|---|
| Phase 0 — support and license gate | Complete | Local, single-user, single-node, non-production learning POC confirmed. Unsupported WSL/RHEL 10/Podman/RTX 5080 risk reviewed; each user must independently review and accept Kinetica's license before installation. |
| Phase 1 — freeze artifacts and persistence | Complete | Official installer downloaded without execution and fingerprinted; CUDA image version/digest pinned; private ext4 persistence directory created. See `KINETICA_ARTIFACTS.md`. |
| Phase 2 — configure and revalidate Podman GPU access | Complete | User-scoped `nvidia` runtime alias configured; named-runtime and CDI tests passed against the same RTX 5080; ports and container name are free. See `PHASE_2_VALIDATION.md`. |
| Phase 3 — install Kinetica | Complete | Pinned CUDA image installed through a locally adapted vendor script on rootless Podman; loopback bindings and ext4 persistence confirmed; five-minute service/GPU stability gate passed. Admin password reset is required in Phase 4. See `PHASE_3_VALIDATION.md`. |
| Phase 4 — functional and GPU validation | Not started | — |
| Phase 5 — POC workload and evidence | Not started | — |
| Phase 6 — operations and rollback | Not started | — |

## Decision

This machine is a **conditional go for a local, non-production proof of concept**. Its CPU, RAM, disk, NVIDIA driver, and GPU-container passthrough are sufficient. It is **not a supported Kinetica configuration** based on the published support matrix:

- Kinetica documents Docker, not Podman, for Developer Edition.
- Kinetica lists RHEL 8.2+/9, not RHEL 10.
- Kinetica does not list WSL as a certified platform.
- Kinetica's tested GPU list does not include the GeForce RTX 5080.

Do not use this topology for production or use it as evidence of a supported configuration. Treat successful database startup and a GPU-backed query as mandatory POC acceptance gates.

## Detected environment

| Area | Detected | Assessment |
|---|---|---|
| Host environment | WSL2, Linux kernel `6.18.33.1-microsoft-standard-WSL2` | CUDA containers work; Kinetica support is unconfirmed |
| Distribution | RHEL 10.2 x86-64 | Not in Kinetica's published RHEL 8.2+/9 list |
| CPU | AMD Ryzen 9 9950X3D; 28 WSL vCPUs; AVX2 present | Pass for a POC |
| Memory | 47 GiB available to WSL; 16 GiB swap | Pass; Developer Edition requires at least 8 GB |
| Storage | About 902 GiB free on Linux ext4 | Pass; exceeds Kinetica's 4× RAM minimum |
| GPU | NVIDIA GeForce RTX 5080, 16,303 MiB VRAM | Visible and usable, but not in Kinetica's tested GPU matrix |
| NVIDIA driver | Windows KMD 610.62; WSL UMD 610.43.02; CUDA capability reported as 13.3 | Exceeds Kinetica's documented 525.x minimum |
| Podman | 5.8.2, rootless, crun, cgroup v2 | Technically suitable, but not vendor-documented for Developer Edition |
| NVIDIA container stack | NVIDIA Container Toolkit 1.19.1; `/etc/cdi/nvidia.yaml` | Pass |
| GPU container test | `podman ... --device nvidia.com/gpu=all ... nvidia-smi` succeeded | Pass |
| systemd | Enabled in WSL | Pass |
| SELinux | Disabled | No label conflict; differs from a normal RHEL host |
| Firewall | firewalld active | Bind only to localhost for the POC |
| Required host ports | 5434, 8000, 8080, 8088, 9191, 9300 currently free | Pass |
| Transparent huge pages | `madvise` | Acceptable for the container POC; Kinetica recommends avoiding `always` |

The persistence directory must remain on the WSL ext4 filesystem. Do not put database files under `/mnt/c`; its 9p filesystem has different correctness and performance characteristics.

## Published requirements

### Hardware

Kinetica's current documentation specifies:

- At least 8 CPU cores on a supported 64-bit CPU; x86-64 must provide AVX2.
- At least 8 GB RAM.
- SSD or 7200-RPM storage sized to at least four times system RAM.
- For GPU builds, NVIDIA driver 525.x or later and one of the tested cards: T4, V100, A10/A40/A100, L4/L40, or H100.

The Developer Edition page independently states that Docker must have at least 8 GB RAM allocated. Sources: [Developer Edition](https://www.kinetica.com/kinetica-developer-edition/), [Kinetica system and GPU requirements](https://docs.kinetica.com/content/install/package), and [GPU driver matrix](https://docs.kinetica.com/content/install/shared/gpu_driver).

The RTX 5080 satisfies the driver requirement but is not a listed/tested card. Driver recency alone does not prove that Kinetica's CUDA binaries support Blackwell. That must be established by the validation phase or confirmed by Kinetica.

### Software

Required for this POC:

- WSL2 with a current Windows NVIDIA driver. Do not install a Linux display driver inside WSL.
- RHEL WSL with systemd and cgroup v2.
- Podman 4.1 or later for CDI; installed version is 5.8.2.
- NVIDIA Container Toolkit and a generated CDI spec.
- Internet access to `files.kinetica.com` and `docker.io/kinetica`.
- Approximately 15 GB temporary image/download headroom plus space for persisted data. The current compressed CUDA image is about 6.2 GB.
- Local availability of TCP ports 5434, 8000, 8080, 8088, 9191, and 9300.

NVIDIA recommends CDI for Podman. Its documented form is `--device nvidia.com/gpu=all`; see [NVIDIA CDI support](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html) and the [NVIDIA Container Toolkit install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html).

WSL has CUDA limitations, including incomplete unified-memory behavior, pinned-memory limits, and incomplete NVML features. These may affect Kinetica even when `nvidia-smi` works. See the [CUDA on WSL guide](https://docs.nvidia.com/cuda/wsl-user-guide/).

### Licensing

The current Kinetica site describes Developer Edition as free forever, local, and single-node, and explicitly shows `./kinetica --gpu start`. The official container includes the developer CUDA license package; the normal Developer Edition flow does not instruct the user to obtain a separate key.

The published developer agreement grants a revocable, non-transferable license for internal development use at no charge. It prohibits commercial exploitation or making the database/software available to third parties, except as the agreement expressly permits. It is not a production or multi-user license. Kinetica reserves the right to change pricing or terms. Review the [Kinetica Developer License Agreement](https://www.kinetica.com/wp-content/uploads/2024/11/Kinetica-Developer-License-Agreement-Dec-10-2021.pdf) before installation.

The current public pages confirm a free single-node GPU flow, but they do **not** state an exact one-GPU entitlement. Do not rely on the phrase "single-GPU license" without confirmation. After launch, inspect the effective license limits in GAdmin; contact `support@kinetica.com` if the permitted GPU count, RAM, data size, organizational use, or redistribution rights matter.

## Podman compatibility design

Use Kinetica's installer rather than constructing a raw `podman run` command. The installer performs UID/GID changes, persistence setup, password initialization, and database configuration that a raw container command would omit.

The official installer supports overriding its container executable with `DOCKER_PROGRAM`, so most Docker CLI calls are compatible with Podman. However, its GPU path appends `--runtime=nvidia`. The current Podman configuration has no runtime named `nvidia`, so the unmodified command fails with:

```text
Error: default OCI runtime "nvidia" not found
```

The installed `/usr/bin/nvidia-container-runtime` was tested successfully when given to Podman by absolute path. The least invasive adapter is therefore a rootless Podman runtime alias:

```toml
[engine.runtimes]
nvidia = ["/usr/bin/nvidia-container-runtime"]
```

Place that in the user's Podman configuration, preserving any existing settings. Verify the alias with the exact CUDA smoke test in Phase 2 before running Kinetica. CDI remains the preferred general Podman GPU method, but the alias matches Kinetica's vendor script without maintaining a patched copy of that script.

## Deployment plan

### Phase 0 — support and license gate

1. Confirm this is personal/internal development, single-node, non-production use.
2. Accept the Developer License Agreement.
3. For a supported outcome, ask Kinetica to confirm RTX 5080, WSL2, RHEL 10, and Podman. If support is required and they do not confirm, stop and move to Docker on a certified RHEL 9 host with a listed GPU.

Exit criterion: unsupported POC risk is explicitly accepted, or Kinetica confirms the topology.

### Phase 1 — freeze artifacts and persistence

1. Download the official installer from `https://files.kinetica.com/install/kinetica.sh`.
2. Record its SHA-256 checksum in the repository.
3. Pin the database image instead of using `latest`. At assessment time, the current image is:

   ```text
   docker.io/kinetica/kinetica-cuda:7.2.3.18.20260707203019.ga
   sha256:b137bb31fe53d325eb30c67eadc12720355df09ad6f512fe99ac5835bf37ffac
   ```

4. Use `${XDG_DATA_HOME:-$HOME/.local/share}/kinetica-poc/persist` on ext4 for persistent data.
5. Keep credentials out of Git. Enter the initial admin password interactively or through a temporary environment value that is immediately unset.

Exit criterion: installer and image digest are recorded, and the persistence path has at least 200 GiB free.

### Phase 2 — configure and revalidate Podman GPU access

1. Add the project-user `nvidia` runtime alias shown above without replacing unrelated Podman configuration.
2. Confirm the GPU runtime alias:

   ```bash
   podman run --rm --runtime=nvidia \
     docker.io/nvidia/cuda:13.0.1-base-ubi9 nvidia-smi
   ```

3. Confirm CDI independently:

   ```bash
   nvidia-ctk cdi list
   podman run --rm --device nvidia.com/gpu=all \
     docker.io/nvidia/cuda:13.0.1-base-ubi9 nvidia-smi
   ```

4. Recheck that ports 5434, 8000, 8080, 8088, 9191, and 9300 are unused.

Exit criterion: both runtime-alias and CDI tests show the RTX 5080 from inside a rootless Podman container.

### Phase 3 — install Kinetica

Run the pinned GPU install through Podman and bind services only to loopback:

```bash
DOCKER_PROGRAM=podman ./kinetica start \
  --gpu \
  --version 7.2.3.18.20260707203019.ga \
   --persist "${XDG_DATA_HOME:-$HOME/.local/share}/kinetica-poc/persist" \
  --listen-address 127.0.0.1
```

Do not expose `0.0.0.0` during the POC. WSL localhost forwarding will make the loopback-bound UIs available to the Windows browser without intentionally exposing them to the LAN.

Initial resource target:

- Leave at least 12–16 GiB of WSL memory available for services, build tools, and filesystem cache.
- Target 28–32 GiB for Kinetica after the first successful default launch.
- Use no more than 20–24 of the 28 WSL vCPUs initially.
- Do not enable swap-dependent benchmark workloads; Kinetica is an in-memory system.

Exit criterion: the container stays running for five minutes and Kinetica reports all ranks healthy.

### Phase 4 — functional and GPU validation

1. Set a non-default admin password.
2. Open Workbench at `http://localhost:8000/workbench/` and GAdmin at `http://localhost:8080/gadmin`.
3. Verify the REST endpoint on `http://localhost:9191` and run `show/system/properties`.
4. In GAdmin, record:
   - Kinetica build/version
   - license edition and limits
   - detected GPU count and model
   - rank-to-GPU assignment
   - configured and effective RAM limit
5. Run a GPU-backed analytic query while monitoring `nvidia-smi`. A successful `nvidia-smi` inside the container is necessary but not sufficient; Kinetica itself must create a CUDA context and perform work.
6. Restart WSL once, then verify container start, persistence, and query correctness again.

Exit criterion: data survives restart, Kinetica uses the GPU without CUDA/NVML errors, and the license reports limits compatible with the POC.

### Phase 5 — POC workload and evidence

1. Build one representative dataset and query suite covering ingest, high-cardinality aggregation/join, geospatial or graph processing, and concurrency.
2. Run the same suite against the CPU image and GPU image with equivalent memory/data settings.
3. Capture query latency, throughput, ingest rate, GPU utilization, GPU memory, CPU, RAM, and failures.
4. Record WSL-specific observations separately; do not generalize WSL results to native Linux production performance.

Exit criterion: a reproducible report shows where GPU execution helps, where it does not, and any WSL/RTX 5080 instability.

### Phase 6 — operations and rollback

Operational commands should continue using the same executable override:

```bash
DOCKER_PROGRAM=podman ./kinetica status
DOCKER_PROGRAM=podman ./kinetica stop
DOCKER_PROGRAM=podman ./kinetica start
```

Before upgrades, back up the persistence directory and pin the new image digest. Test upgrades on a copy of the data first. Rollback means stopping/removing the container and recreating it from the prior pinned image while retaining a compatible persistence backup. Never use the installer's `clear` command unless deletion of all POC data is intentional and a backup has been verified.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| RTX 5080 is absent from Kinetica's GPU matrix | CUDA startup or kernel incompatibility | Mandatory Kinetica GPU-query test; seek vendor confirmation |
| WSL CUDA limitations | Performance anomalies or memory-related failure | Keep datasets bounded; monitor memory; reproduce critical results on native Linux |
| Podman is not vendor-documented | Installer lifecycle commands may differ from Docker | Use the vendor installer with only the runtime alias; test install/start/stop/restart/upgrade behavior |
| RHEL 10 is not certified | Host integration/support issues | Container isolation reduces but does not remove risk; use RHEL 9 for a supported deployment |
| Rootless `--privileged` is not equivalent to rootful Docker | Missing kernel privileges or limits | Treat successful database/rank startup as a gate; use a dedicated rootful Podman service only if a specific failure proves it necessary |
| Default installer binds to all interfaces | Unintended network exposure | Force `--listen-address 127.0.0.1`; do not add public firewall rules |
| `latest` tag changes | Non-reproducible POC | Pin full version and digest |
| Developer license scope is misunderstood | License violation | Keep it internal/non-production; inspect effective limits; obtain written confirmation for ambiguous use |

## Final recommendation

Proceed with the Podman-based POC only after Phase 0. The machine has ample resources and its GPU passthrough is proven. The first real technical gate is Kinetica creating a CUDA context on the RTX 5080; the first governance gate is confirmation that the intended organizational use fits the Developer License Agreement. If either fails, use the CPU Developer Edition for API/SQL learning or move the GPU POC to certified RHEL 9 hardware with a listed NVIDIA GPU.
