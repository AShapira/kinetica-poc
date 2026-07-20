# Phase 3 validation — Kinetica installation

Completed: 2026-07-19

## Installed artifact

- Image: `docker.io/kinetica/kinetica-cuda:7.2.3.18.20260707203019.ga`
- Verified registry digest: `sha256:b137bb31fe53d325eb30c67eadc12720355df09ad6f512fe99ac5835bf37ffac`
- Local image ID: `dce44a69181f84d063179de5920a8bcae4165c94ac77c79d1f1a22bc61278860`
- Local unpacked image size reported by Podman: 12,810,087,327 bytes
- Container name: `kinetica`
- Persistence: `${XDG_DATA_HOME:-$HOME/.local/share}/kinetica-poc/persist/kinetica-persist`

## Rootless Podman remediation

The first initialization attempt failed before Kinetica became usable. Two
Docker/Podman compatibility differences were identified:

1. Podman repeats environment variables and labels in `podman inspect` under
   `CreateCommand`. The Docker-oriented Kinetica parser concatenated both
   matches, creating newline-containing user names and paths.
2. A rootless Podman bind mount requires a keep-ID user namespace for the
   remapped `gpudb` UID to match the host owner. Container root must also be
   selected explicitly during Kinetica's initialization.

The incomplete container contained no user data. It was stopped, removed, and
only its generated partial `kinetica-persist` directory was deleted before the
clean retry. The nonresponsive failed container required SIGKILL after the
15-second graceful stop timeout. The pinned image was retained.

The upstream installer was preserved locally. A local compatibility adjustment
made its inspect parser select the first matching value. The vendor installer
and derived patch are intentionally not distributed in this public repository.
The upstream checksum is recorded in `KINETICA_ARTIFACTS.md`.

The clean installation added these Podman run arguments:

```text
--userns=keep-id --user=0
```

A disposable preflight proved that this combination allowed the remapped
`gpudb` user to write an ext4 bind mount while retaining GPU visibility.

## Installation command shape

The installation used the patched vendor script with:

```bash
DOCKER_PROGRAM=podman ./kinetica start \
  --gpu \
  --version 7.2.3.18.20260707203019.ga \
  --persist "${XDG_DATA_HOME:-$HOME/.local/share}/kinetica-poc/persist" \
  --listen-address 127.0.0.1 \
  --docker-run-args "--userns=keep-id --user=0"
```

A strong random initialization password was generated inside the installation
process, redacted from returned output, and discarded. It is not stored. The
admin password must be reset in Phase 4 before UI or authenticated API use.

## Network bindings

Podman publishes these ports only on loopback:

| Host | Container | Service |
|---|---|---|
| `127.0.0.1:5434` | `5432/tcp` | PostgreSQL wire protocol |
| `127.0.0.1:8000` | `8000/tcp` | Workbench |
| `127.0.0.1:8080` | `8080/tcp` | GAdmin |
| `127.0.0.1:8088` | `8088/tcp` | Reveal |
| `127.0.0.1:9191` | `9191/tcp` | Database REST/API |

No service was bound to a non-loopback host address.

## Health and stability

Kinetica reported these enabled services running:

- Host Manager
- Supervisor
- Tomcat/GAdmin
- Stats
- Workbench
- GPUdb
- Text Search
- Reveal
- Query Planner
- Graph Server

MCP, MCP OAuth, HTTPD, Ranger Authorizer, and ML Server were stopped or not
enabled, which is expected for the current configuration.

The live container reported:

```text
NVIDIA GeForce RTX 5080
<GPU-UUID>
16303 MiB
```

No fatal CUDA, NVML, GPU, or persistence-permission error was found in the
recent Kinetica core logs.

The five-minute stability gate ran from container start at
`2026-07-19T21:34:47+03:00` through the final check at
`2026-07-19T21:40:05+03:00`. At every checkpoint:

- the container remained running;
- the original start time was unchanged;
- `OOMKilled` remained false; and
- Kinetica's `is-running` check succeeded.

## Phase result

Phase 3 passed. Installation and service startup are successful on rootless
Podman, and Kinetica can enumerate the unsupported RTX 5080. A GPU-backed
database query has not yet been executed; that remains a Phase 4 acceptance
gate.

The container currently has no explicit Podman CPU or memory limit and inherits
the WSL allocation of 28 CPUs and 48 GB RAM. Resource tuning should follow
functional validation rather than changing limits during first startup.
