import csv
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import sedona_benchmark.building_sources as building_sources
from sedona_benchmark.building_sources import analyze_building_sources
from sedona_benchmark.config import BenchmarkConfig

SOURCE_ITEM_TYPE = pa.struct(
    [
        pa.field("property", pa.string()),
        pa.field("dataset", pa.string()),
        pa.field("license", pa.string()),
        pa.field("record_id", pa.string()),
        pa.field("update_time", pa.string()),
        pa.field("confidence", pa.float64()),
        pa.field("between", pa.list_(pa.float64())),
    ]
)
SOURCES_TYPE = pa.list_(SOURCE_ITEM_TYPE)


def _source(
    dataset: str | None,
    property_name: str | None,
    *,
    license_name: str | None = None,
    record_id: str | None = None,
    update_time: str | None = None,
    confidence: float | None = None,
    between: list[float] | None = None,
) -> dict:
    return {
        "property": property_name,
        "dataset": dataset,
        "license": license_name,
        "record_id": record_id,
        "update_time": update_time,
        "confidence": confidence,
        "between": between,
    }


def _write_buildings(path: Path, rows: list[list[dict | None] | None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "id": pa.array([f"building-{index}" for index in range(len(rows))]),
            "sources": pa.array(rows, type=SOURCES_TYPE),
        }
    )
    pq.write_table(table, path, row_group_size=2)


def _config(source_root: Path, output_root: Path) -> BenchmarkConfig:
    values = {
        "source": {
            "overture_release": "test-release",
            "root": str(source_root),
            "building_glob": "theme=buildings/type=building/*.parquet",
        },
        "output": {"root": str(output_root)},
    }
    return BenchmarkConfig(
        path=source_root / "benchmark.yaml",
        raw_text=json.dumps(values, sort_keys=True),
        values=values,
    )


def _fixture_files(source_root: Path) -> None:
    directory = source_root / "theme=buildings" / "type=building"
    whole_a = _source(
        "A",
        "",
        license_name="L1",
        record_id="a-1",
        update_time="2026-01-01T00:00:00Z",
        confidence=0.8,
    )
    height_b = _source(
        "B",
        "/height",
        license_name="L2",
        record_id="b-1",
        update_time="2026-02-01T00:00:00Z",
        confidence=0.5,
        between=[0.0, 1.0],
    )
    _write_buildings(
        directory / "part-00000.parquet",
        [
            [whole_a],
            [whole_a, height_b],
            [],
            None,
            [None],
        ],
    )
    _write_buildings(
        directory / "part-00001.parquet",
        [
            [height_b, whole_a],
            [_source("A", "", license_name="L3")],
            [_source(None, None)],
        ],
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_aggregates_catalog_combinations_cardinality_and_anomalies(
    tmp_path: Path,
):
    source_root = tmp_path / "source"
    output_root = tmp_path / "output"
    _fixture_files(source_root)

    result = analyze_building_sources(
        _config(source_root, output_root),
        workers=1,
        batch_size=2,
    )

    output_dir = Path(result["output_dir"])
    summary = json.loads((output_dir / "summary.json").read_text())
    assert result["feature_count"] == 8
    assert result["source_item_count"] == 8
    assert summary["complete"] is True
    assert summary["validation"]["passed"] is True
    assert summary["anomalies"] == {
        "empty_source_lists": 1,
        "missing_dataset_items": 2,
        "missing_property_items": 2,
        "null_source_items": 1,
        "null_source_lists": 1,
    }

    catalog = _read_csv(output_dir / "source-catalog.csv")
    whole = next(
        row
        for row in catalog
        if row["dataset"] == "A" and row["property_scope"] == "whole_feature"
    )
    assert whole["source_item_count"] == "4"
    assert whole["feature_count"] == "4"
    assert whole["first_position_count"] == "3"
    assert whole["later_position_count"] == "1"
    assert json.loads(whole["licenses_json"]) == ["L1", "L3"]

    combinations = _read_csv(output_dir / "source-combinations.csv")
    combined = next(row for row in combinations if row["source_count"] == "2")
    assert combined["feature_count"] == "2"
    assert json.loads(combined["source_keys_json"]) == [
        ["A", ""],
        ["B", "/height"],
    ]
    assert sum(int(row["feature_count"]) for row in combinations) == 8

    cardinality = _read_csv(output_dir / "source-cardinality.csv")
    counts = {
        (row["status"], row["source_count"]): int(row["feature_count"])
        for row in cardinality
    }
    assert counts == {
        ("valid", "0"): 1,
        ("valid", "1"): 4,
        ("valid", "2"): 2,
        ("null", ""): 1,
    }


def test_matching_checkpoints_are_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source_root = tmp_path / "source"
    output_root = tmp_path / "output"
    _fixture_files(source_root)
    config = _config(source_root, output_root)
    analyze_building_sources(config, workers=1, batch_size=2)

    def fail_scan(*args, **kwargs):
        raise AssertionError("matching checkpoints should prevent rescanning")

    monkeypatch.setattr(building_sources, "_scan_file", fail_scan)
    result = analyze_building_sources(config, workers=1, batch_size=17)

    assert result["reused_checkpoint_count"] == 2


def test_changed_input_invalidates_only_its_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source_root = tmp_path / "source"
    output_root = tmp_path / "output"
    _fixture_files(source_root)
    config = _config(source_root, output_root)
    analyze_building_sources(config, workers=1, batch_size=2)
    changed = (
        source_root
        / "theme=buildings"
        / "type=building"
        / "part-00001.parquet"
    )
    _write_buildings(changed, [[_source("changed", "")]])
    original = building_sources._scan_file
    scanned = []

    def record_scan(path, *args, **kwargs):
        scanned.append(path.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(building_sources, "_scan_file", record_scan)
    result = analyze_building_sources(config, workers=1, batch_size=2)

    assert scanned == ["part-00001.parquet"]
    assert result["reused_checkpoint_count"] == 1
    assert result["feature_count"] == 6


def test_worker_count_does_not_change_csv_results(tmp_path: Path):
    source_root = tmp_path / "source"
    _fixture_files(source_root)
    first = analyze_building_sources(
        _config(source_root, tmp_path / "output-1"),
        workers=1,
        batch_size=2,
    )
    second = analyze_building_sources(
        _config(source_root, tmp_path / "output-2"),
        workers=2,
        batch_size=3,
    )

    for name in (
        "source-catalog.csv",
        "source-combinations.csv",
        "source-cardinality.csv",
    ):
        assert (Path(first["output_dir"]) / name).read_bytes() == (
            Path(second["output_dir"]) / name
        ).read_bytes()


def test_smoke_output_is_incomplete_and_isolated(tmp_path: Path):
    source_root = tmp_path / "source"
    output_root = tmp_path / "output"
    _fixture_files(source_root)

    result = analyze_building_sources(
        _config(source_root, output_root),
        workers=1,
        batch_size=1,
        smoke=True,
    )

    assert result["complete"] is False
    assert result["file_count"] == 1
    assert result["feature_count"] == 2
    assert Path(result["output_dir"]).name == "smoke"
    assert not Path(result["output_dir"]).parent.joinpath("summary.json").exists()


def test_invalid_sources_schema_fails_before_outputs(tmp_path: Path):
    source_root = tmp_path / "source"
    output_root = tmp_path / "output"
    path = (
        source_root
        / "theme=buildings"
        / "type=building"
        / "part-00000.parquet"
    )
    path.parent.mkdir(parents=True)
    pq.write_table(pa.table({"sources": pa.array(["wrong"])}), path)

    with pytest.raises(ValueError, match="sources must be a list"):
        analyze_building_sources(
            _config(source_root, output_root),
            workers=1,
        )

    assert not output_root.exists()
