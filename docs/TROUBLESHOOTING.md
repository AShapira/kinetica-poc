# Troubleshooting

## Podman cannot access its runtime directory

If a sandboxed command reports a read-only `/run/user/.../libpod`, run Podman
through the approved host execution path. Do not switch the project to Docker
or rootful Podman.

## Image build fails at Python resolution

Confirm Python 3.12 and the exact versions in `pyproject.toml`. Rebuild without
changing a version first; transient registry failures should be retried. Any
version change belongs in `IMPLEMENTATION_DECISIONS.md` and requires a new
environment manifest.

## SedonaDB cannot find PROJ

The DB image contains system `libproj` and `proj-data`. Use
`sedona.db.configure_proj("system")` only if auto-detection fails, then record
the PROJ library and database path.

## SedonaSpark Jupyter exits or optional services fail

Use `./sedona.sh start-spark`, which overrides the official image's
multi-service root startup with rootless Jupyter and explicit Spark
`local[*]`. Confirm `runtime/spark-home` is mode `0700` and writable by the
current user. Do not re-enable the image's SSH, Zeppelin, or standalone
master/worker startup and then describe their permission errors as a healthy
learning deployment.

## Overture query returns no boundary

Inspect the `division_area` schema and distinct `country`, `subtype`, and
`is_land` values. Do not silently substitute an online boundary. A source
schema change is a principal data-definition issue.

## Bbox fields do not prune

Check the execution plan for predicates on all four nested bbox fields. Confirm
the input preserves GeoParquet covering metadata. If exact results are correct
but pruning is absent, the performance preparation gate still fails.

## Road output contains collections

Clipping can produce multilines and geometry collections. The normalizer keeps
only valid, non-empty LineString parts and assigns a part suffix to the source
ID. Compare pre/post counts and total length before continuing.

## Locations are not repeatable

Verify PCG64, seed, EPSG:2039, boundary checksum, target count, and deterministic
row ordering. Any change to one of these requires a new dataset ID.

## Kinetica authentication fails

Verify the secret file exists, has mode `0600`, contains no newline-sensitive
extra data, and matches the locally reset administrator credential. Never add
the password to `.env`, YAML, a notebook, or a command argument.

## Kinetica radius discovery keeps expanding

Confirm the selected tier contains roads and every location is inside the
canonical boundary. Preserve the uncovered IDs. A radius over 1024 km aborts
instead of hiding a data or SQL error.

## Results disagree

Join by location ID and retain both road IDs, points, and distances. Inspect
ties and overlapping centerlines first. Then run exhaustive distance ranking
for the failing IDs. Do not adjust the tolerance above 1 metre to make the test
pass.

## Sedona becomes slower with more threads

Check the recorded affinity against `lscpu`, verify Kinetica is stopped, and
inspect per-repetition RSS, relative MAD, and retained spill. Do not delete slow
late repetitions. The reference host showed a stable physical-core knee at
8–12 cores and severe memory pressure at 28 SMT threads; another host may have
a different knee.

## Notebook output is too large or machine-specific

Display summaries and small samples, not raw frames. Replace absolute host
paths with environment-variable names. Clear widget state and high-volume logs,
then execute again.
