# Pinned Kinetica artifacts

Recorded: 2026-07-19

## Installer

- Source: `https://files.kinetica.com/install/kinetica.sh`
- SHA-256 at validation time: `ed5ba2034a47c61ff46550fb8c41afd1d907da0bb7acbed6f117fa78c78b0f88`
- Size: 85,152 bytes
- Upstream `Last-Modified`: `Wed, 13 May 2026 16:29:18 GMT`
- Upstream ETag: `"6a04a6de-14ca0"`
- Embedded installer version: `7.2.0.0-20260513162806`
- Syntax validation: `bash -n` passed

The upstream URL is mutable. Download the installer locally and verify its
checksum before use. The installer is deliberately excluded from this
repository.

```bash
curl --proto '=https' --tlsv1.2 --fail --location \
  https://files.kinetica.com/install/kinetica.sh \
  --output kinetica
chmod 0755 kinetica
sha256sum kinetica
```

If the checksum differs, inspect the new upstream artifact and update this
record intentionally rather than assuming the change is safe.

## CUDA image

- Image: `docker.io/kinetica/kinetica-cuda:7.2.3.18.20260707203019.ga`
- Manifest digest: `sha256:b137bb31fe53d325eb30c67eadc12720355df09ad6f512fe99ac5835bf37ffac`
- Kinetica version label: `7.2.3.18.20260707203019.ga`
- Created: `2026-07-12T01:48:46.860566969Z`
- Platform: `linux/amd64`
- Base OS label: Ubuntu 24.04

The deployment must use the full version tag above. Verify that the registry
still resolves it to the recorded digest before installation.

```bash
skopeo inspect \
  docker://docker.io/kinetica/kinetica-cuda:7.2.3.18.20260707203019.ga \
  | jq -r '.Digest'
```

## Persistence

Use an ext4-backed path inside the WSL distribution, for example:

```text
${XDG_DATA_HOME:-$HOME/.local/share}/kinetica-poc/persist
```

Do not store database persistence under `/mnt/c`.
