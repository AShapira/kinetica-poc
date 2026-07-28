import json
from pathlib import Path

from shapely.geometry import LineString, MultiLineString, Polygon

from sedona_benchmark.config import BenchmarkConfig
from sedona_benchmark.prepare import _line_parts, _refresh_full_dataset_index


def test_line_parts_handles_line_and_multi_line():
    first = LineString([(0, 0), (1, 1)])
    second = LineString([(1, 1), (2, 1)])
    assert _line_parts(first) == [first]
    assert _line_parts(MultiLineString([first, second])) == [first, second]


def test_line_parts_drops_non_lines():
    assert _line_parts(Polygon([(0, 0), (1, 0), (0, 1)])) == []


def test_full_dataset_index_never_points_to_smoke_locations(tmp_path: Path):
    config = BenchmarkConfig(
        path=tmp_path / "benchmark.yaml",
        raw_text="schema_version: 1\n",
        values={
            "output": {"root": str(tmp_path)},
            "locations": {"count": 10_000},
        },
    )
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    for name in (
        "israel_boundary.parquet",
        "israel_roads.parquet",
        "israel_locations_100.parquet",
        "israel_locations_10000.parquet",
    ):
        (canonical / name).touch()

    index = _refresh_full_dataset_index(config)

    assert index is not None
    assert index["locations"].endswith("israel_locations_10000.parquet")
    saved = json.loads((tmp_path / "manifests" / "dataset-index.json").read_text())
    assert saved == index
