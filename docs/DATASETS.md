# Dataset catalog and lineage

## Stable identifiers

- `overture-2026-07-22.0-il-boundary-v1`
- `overture-2026-07-22.0-il-roads-v1`
- `il-locations-n10000-seed20260727-v1`

Changing the source release, road semantics, clipping, seed, or schema requires
a new identifier. Full files remain external; manifests and small samples are
committed.

## Overture inputs

`division_area` supplies `id`, `country`, `subtype`, `is_land`, and geometry.
Selection is `country='IL'`, `subtype='country'`, `is_land=true`. This records
the exact Overture boundary and is not a geopolitical assertion.

Transportation `segment` supplies `id`, `subtype`, `class`, `bbox`, and
geometry. Candidate filtering uses road subtype, selected classes, and all four
scalar bbox-overlap predicates. Exact intersection and clipping follow.

## Intermediate data

The boundary must contain exactly one non-empty valid feature. The clipped-road
intermediate contains source IDs, classes, and exact boundary intersections.
Its plan is retained to audit the global scan.

```text
segment.xmin <= israel.xmax
segment.xmax >= israel.xmin
segment.ymin <= israel.ymax
segment.ymax >= israel.ymin
```

All four predicates are necessary to retain long features that cross the
boundary.

## Canonical roads

| Field | Meaning |
|---|---|
| `road_id` | source segment plus clipped-part number |
| `source_segment_id` | Overture feature ID |
| `road_class` | normalized Overture class |
| `is_arterial` | membership in arterial tier |
| `is_general_driving` | membership in headline tier |
| `is_service_rural` | membership in broad tier |
| `length_m` | EPSG:2039 quality statistic |
| `geometry` | clipped EPSG:4326 LineString |
| `bbox` | GeoParquet 1.1 covering |

| Tier | Classes |
|---|---|
| arterial | motorway, trunk, primary, secondary |
| general driving | arterial + tertiary, unclassified, residential, living street |
| service/rural | general driving + service, track |

Pedestrian, path, cycle, bridle, raceway, and unknown classes are excluded.
General driving is the headline because primary roads alone do not represent
normal vehicle access.

## Canonical locations

Locations contain ID, seed, longitude, latitude, EPSG:4326 point, and bbox.
They are sampled uniformly by area in EPSG:2039 using NumPy PCG64 seed
`20260727`, then transformed. Degree-space sampling is intentionally avoided.

## Results

Each engine returns location ID, road ID and class, closest point, and
spherical distance in metres. Row-level results stay outside Git. Checksums,
statistics, mismatches, and diagnostics remain in curated evidence.

## Manifests

Each manifest records lineage, source release, generation command,
configuration hash, Git commit, files, sizes, SHA-256, bounds, CRS, row count,
geometry type, duration, statistics, and validation. A failed validation
prohibits downstream benchmarking.
