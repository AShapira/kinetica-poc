import json
from pathlib import Path

import pytest
from shapely.geometry import box

from sedona_benchmark.benchmark import _presentation_runs
from sedona_benchmark.config import BenchmarkConfig
from sedona_benchmark.paths import canonical_paths, dataset_manifest_names
from sedona_benchmark.presentation_bundle import _selected_boundary


def _config(tmp_path: Path, slug: str = "ashdod-10km") -> BenchmarkConfig:
    return BenchmarkConfig(
        path=tmp_path / "benchmark.yaml",
        raw_text="schema_version: 1\n",
        values={
            "scope": {"slug": slug},
            "output": {"root": str(tmp_path)},
            "locations": {"count": 1_000},
            "roads": {"headline_tier": "general_driving"},
            "benchmark": {"repetitions": 3},
        },
    )


def test_scoped_paths_keep_production_defaults_stable(tmp_path: Path):
    production = _config(tmp_path, slug="israel")
    presentation = _config(tmp_path)

    assert canonical_paths(production).roads.name == "israel_roads.parquet"
    assert canonical_paths(presentation).locations.name == (
        "ashdod-10km_locations_1000.parquet"
    )
    assert dataset_manifest_names(presentation) == (
        "ashdod-10km_boundary.json",
        "ashdod-10km_roads.json",
        "ashdod-10km_locations_1000.json",
    )


def test_selected_boundary_resolves_exact_ashdod_and_clips_to_country():
    locality_id = "248ef38a-2ef7-4f18-9cf4-71a8f9b1d9ab"
    rows = [
        {
            "id": locality_id,
            "country": "IL",
            "subtype": "locality",
            "is_land": True,
            "names": {"common": {"en": "Ashdod"}},
            "geometry": box(34.64, 31.78, 34.68, 31.82).wkb,
        },
        {
            "id": "israel-country",
            "country": "IL",
            "subtype": "country",
            "is_land": True,
            "names": {"common": {"en": "Israel"}},
            "geometry": box(34.5, 31.6, 34.8, 32.0).wkb,
        },
    ]
    settings = {
        "locality_id": locality_id,
        "locality_name_en": "Ashdod",
        "country": "IL",
        "subtype": "country",
        "buffer_m": 10_000,
        "clip_to_country_land": True,
        "projected_crs": "EPSG:2039",
    }

    boundary, metadata = _selected_boundary(rows, settings)

    assert len(boundary) == 1
    assert boundary.crs.to_epsg() == 4326
    assert boundary.geometry.is_valid.all()
    assert 0 < metadata["area_km2"] < 1_000
    assert metadata["locality_name_en"] == "Ashdod"
    assert metadata["country_division_id"] == "israel-country"


def _run(engine: str, created_at: str, bundle: str = "same") -> dict:
    return {
        "engine": engine,
        "created_at": created_at,
        "location_count": 1_000,
        "repetitions": 3,
        "road_tier": "general_driving",
        "correctness": {"passed": True},
        "environment": {
            "benchmark_profile": "windows-docker-presentation",
            "bundle_sha256": bundle,
        },
    }


def test_presentation_runs_select_latest_matching_pair(tmp_path: Path):
    runs = tmp_path / "runs"
    runs.mkdir()
    values = {
        "old-sedona.json": _run("sedonadb", "2026-01-01T00:00:00Z"),
        "new-sedona.json": _run("sedonadb", "2026-01-02T00:00:00Z"),
        "kinetica.json": _run("kinetica", "2026-01-01T00:00:00Z"),
    }
    for name, value in values.items():
        (runs / name).write_text(json.dumps(value), encoding="utf-8")

    selected = _presentation_runs(_config(tmp_path))

    assert [value["engine"] for value in selected] == ["sedonadb", "kinetica"]
    assert selected[0]["created_at"] == "2026-01-02T00:00:00Z"


def test_presentation_runs_reject_different_bundles(tmp_path: Path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "sedona.json").write_text(
        json.dumps(_run("sedonadb", "2026-01-01T00:00:00Z", "first")),
        encoding="utf-8",
    )
    (runs / "kinetica.json").write_text(
        json.dumps(_run("kinetica", "2026-01-01T00:00:00Z", "second")),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="one bundle hash"):
        _presentation_runs(_config(tmp_path))
