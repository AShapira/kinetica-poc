# Phase 2 validation — Podman GPU compatibility

Completed: 2026-07-19

## Configuration change

Created the user-scoped Podman configuration:

`${XDG_CONFIG_HOME:-$HOME/.config}/containers/containers.conf`

```toml
# User-scoped compatibility alias for software that requests
# `podman run --runtime=nvidia`. Podman's default runtime remains crun.
[engine.runtimes]
nvidia = ["/usr/bin/nvidia-container-runtime"]
```

There was no pre-existing user `containers.conf`, so no prior configuration was
replaced. Podman's default OCI runtime remains `/usr/bin/crun`; the alias is used
only when a command explicitly requests `--runtime=nvidia`.

## Named runtime test

Command:

```bash
podman run --rm --runtime=nvidia \
  docker.io/nvidia/cuda:13.0.1-base-ubi9 \
  nvidia-smi --query-gpu=name,uuid,driver_version,memory.total \
  --format=csv,noheader
```

Result: passed.

```text
NVIDIA GeForce RTX 5080, <GPU-UUID>, 610.62, 16303 MiB
```

## CDI test

Command:

```bash
podman run --rm --device nvidia.com/gpu=all \
  docker.io/nvidia/cuda:13.0.1-base-ubi9 \
  nvidia-smi --query-gpu=name,uuid,driver_version,memory.total \
  --format=csv,noheader
```

Result: passed with the same GPU UUID, driver, and memory as the named-runtime
test.

## Kinetica installer compatibility check

Command:

```bash
DOCKER_PROGRAM=podman ./kinetica status --gpu
```

Result: the installer accepted Podman and the GPU prerequisites, reported
`Not installed, rerun with 'start'`, and returned its documented not-installed
status code `2`. It did not install or start Kinetica.

## Port and name checks

The following loopback host ports were free at validation time:

```text
5434, 8000, 8080, 8088, 9191, 9300
```

The Podman container name `kinetica` was also free.

## Phase result

Phase 2 passed. The environment now supports both the older named NVIDIA runtime
expected by Kinetica's installer and NVIDIA's preferred CDI path for Podman. The
next gate is Phase 3, where the pinned Kinetica CUDA image will be pulled and the
database will attempt to create a CUDA context on the unsupported RTX 5080.
