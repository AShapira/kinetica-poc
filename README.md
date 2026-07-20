# Kinetica GPU POC on Podman and RHEL WSL2

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

