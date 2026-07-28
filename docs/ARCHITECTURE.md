# Architecture and data flow

## Components

**SedonaDB** performs global GeoParquet pruning, exact clipping, deterministic
point generation, exact range-join execution, validation, notebooks, and
reporting.

**SedonaSpark** provides a one-master/one-worker learning cluster. Its notebook
explains partitions, spatial SQL, GeoParquet, and why Spark's non-point KNN
semantics are not used for the headline line-distance benchmark.

**Kinetica** is the existing 7.2.3 GPU POC. Canonical rows are loaded through
Kinetica's DB-API. Its equivalent workflow uses a proved candidate radius,
`ST_DWITHIN`, exact distance ranking, and `ST_CLOSESTPOINT`.

**External storage** separates the read-only Overture release, regenerable
benchmark data, and curated repository evidence.

## Data flow

```text
divisions -> country/is_land filter -------------------> IL boundary
segments  -> scalar bbox overlap -> exact intersection -> clipped roads
IL boundary -> EPSG:2039 seeded rejection sampling ---> locations
roads + locations -> SedonaDB radius ranking --+
roads + locations -> Kinetica radius ranking ---+--> correctness + summaries
```

Scalar bbox fields and Kinetica's radius are candidate filters only. Exact
geometry operations decide inclusion and nearest distance.

## CRS and distance

Canonical geometry uses EPSG:4326. Sampling and quality lengths use EPSG:2039
so planar area and length have local metric meaning. Measured nearest distances
use spherical functions and metres.

## Reproducibility boundary

A run is identified by source and canonical checksums, configuration hash, Git
commit, image digest, package and engine versions, CPU topology and affinity,
road tier, seed, warm-up count, and repetitions. Summary rows are comparable
only when all fields except the intended independent variable agree.
