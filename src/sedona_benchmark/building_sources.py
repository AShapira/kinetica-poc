"""Bounded-memory aggregation of Overture building source provenance."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from sedona_benchmark.config import BenchmarkConfig
from sedona_benchmark.manifest import git_commit, sha256_file, utc_now

SCANNER_SCHEMA_VERSION = 1
DEFAULT_WORKERS = 4
DEFAULT_BATCH_SIZE = 65_536
_NUMERIC_CATALOG_FIELDS = (
    "source_item_count",
    "feature_count",
    "first_position_count",
    "later_position_count",
    "record_id_present_count",
    "update_time_present_count",
    "confidence_count",
    "confidence_sum",
    "between_present_count",
)
_ANOMALY_FIELDS = (
    "null_source_lists",
    "empty_source_lists",
    "null_source_items",
    "missing_dataset_items",
    "missing_property_items",
)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )


def _source_key_json(dataset: str | None, property_name: str | None) -> str:
    return json.dumps(
        [dataset, property_name],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _combination_json(keys: list[tuple[str | None, str | None]]) -> str:
    ordered = sorted(keys, key=lambda value: _source_key_json(*value))
    return json.dumps(
        [[dataset, property_name] for dataset, property_name in ordered],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _license_sort_key(value: str | None) -> tuple[bool, str]:
    return value is not None, value or ""


def _empty_catalog_entry() -> dict[str, Any]:
    return {
        "source_item_count": 0,
        "feature_count": 0,
        "first_position_count": 0,
        "later_position_count": 0,
        "licenses": [],
        "record_id_present_count": 0,
        "update_time_present_count": 0,
        "update_time_min": None,
        "update_time_max": None,
        "confidence_count": 0,
        "confidence_sum": 0.0,
        "confidence_min": None,
        "confidence_max": None,
        "between_present_count": 0,
    }


def _catalog_entry(catalog: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    return catalog.setdefault(key, _empty_catalog_entry())


def _merge_optional_min(current: Any, incoming: Any) -> Any:
    if current is None:
        return incoming
    if incoming is None:
        return current
    return min(current, incoming)


def _merge_optional_max(current: Any, incoming: Any) -> Any:
    if current is None:
        return incoming
    if incoming is None:
        return current
    return max(current, incoming)


def _merge_catalog_entry(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for name in _NUMERIC_CATALOG_FIELDS:
        target[name] += incoming[name]
    target["licenses"] = sorted(
        set(target["licenses"]) | set(incoming["licenses"]),
        key=_license_sort_key,
    )
    target["update_time_min"] = _merge_optional_min(
        target["update_time_min"], incoming["update_time_min"]
    )
    target["update_time_max"] = _merge_optional_max(
        target["update_time_max"], incoming["update_time_max"]
    )
    target["confidence_min"] = _merge_optional_min(
        target["confidence_min"], incoming["confidence_min"]
    )
    target["confidence_max"] = _merge_optional_max(
        target["confidence_max"], incoming["confidence_max"]
    )


def _sum_true(values: pa.Array | pa.ChunkedArray) -> int:
    result = pc.sum(pc.cast(values, pa.int64())).as_py()
    return int(result or 0)


def _group_count(
    dataset: pa.Array | pa.ChunkedArray,
    property_name: pa.Array | pa.ChunkedArray,
) -> list[dict[str, Any]]:
    return (
        pa.table({"dataset": dataset, "property": property_name})
        .group_by(["dataset", "property"])
        .aggregate([([], "count_all")])
        .to_pylist()
    )


def _aggregate_flat_sources(
    flat: pa.Array | pa.ChunkedArray,
    catalog: dict[str, dict[str, Any]],
) -> None:
    if len(flat) == 0:
        return
    dataset = pc.struct_field(flat, "dataset")
    property_name = pc.struct_field(flat, "property")
    license_name = pc.struct_field(flat, "license")
    record_id = pc.struct_field(flat, "record_id")
    update_time = pc.struct_field(flat, "update_time")
    confidence = pc.struct_field(flat, "confidence")
    between = pc.struct_field(flat, "between")
    table = pa.table(
        {
            "dataset": dataset,
            "property": property_name,
            "record_id_present": pc.cast(pc.is_valid(record_id), pa.int64()),
            "update_time_present": pc.cast(pc.is_valid(update_time), pa.int64()),
            "update_time": update_time,
            "confidence_present": pc.cast(pc.is_valid(confidence), pa.int64()),
            "confidence": confidence,
            "between_present": pc.cast(pc.is_valid(between), pa.int64()),
        }
    )
    grouped = table.group_by(["dataset", "property"]).aggregate(
        [
            ([], "count_all"),
            ("record_id_present", "sum"),
            ("update_time_present", "sum"),
            ("update_time", "min"),
            ("update_time", "max"),
            ("confidence_present", "sum"),
            ("confidence", "sum"),
            ("confidence", "min"),
            ("confidence", "max"),
            ("between_present", "sum"),
        ]
    )
    for row in grouped.to_pylist():
        key = _source_key_json(row["dataset"], row["property"])
        incoming = _empty_catalog_entry()
        incoming.update(
            {
                "source_item_count": int(row["count_all"]),
                "record_id_present_count": int(row["record_id_present_sum"] or 0),
                "update_time_present_count": int(
                    row["update_time_present_sum"] or 0
                ),
                "update_time_min": row["update_time_min"],
                "update_time_max": row["update_time_max"],
                "confidence_count": int(row["confidence_present_sum"] or 0),
                "confidence_sum": float(row["confidence_sum"] or 0.0),
                "confidence_min": row["confidence_min"],
                "confidence_max": row["confidence_max"],
                "between_present_count": int(row["between_present_sum"] or 0),
            }
        )
        _merge_catalog_entry(_catalog_entry(catalog, key), incoming)

    licenses = (
        pa.table(
            {
                "dataset": dataset,
                "property": property_name,
                "license": license_name,
            }
        )
        .group_by(["dataset", "property", "license"])
        .aggregate([([], "count_all")])
    )
    for row in licenses.to_pylist():
        key = _source_key_json(row["dataset"], row["property"])
        entry = _catalog_entry(catalog, key)
        entry["licenses"] = sorted(
            set(entry["licenses"]) | {row["license"]},
            key=_license_sort_key,
        )


def _key_match(
    values: pa.Array | pa.ChunkedArray,
    expected: str | None,
) -> pa.Array | pa.ChunkedArray:
    if expected is None:
        return pc.is_null(values)
    return pc.fill_null(pc.equal(values, expected), False)


def _aggregate_multi_source_lists(
    multi_lists: pa.Array,
    catalog: dict[str, dict[str, Any]],
    combinations: dict[str, int],
) -> None:
    """Aggregate multi-source rows without materializing full source structs."""
    if len(multi_lists) == 0:
        return
    flat = pc.list_flatten(multi_lists)
    dataset = pc.struct_field(flat, "dataset")
    property_name = pc.struct_field(flat, "property")
    source_keys = [
        (row["dataset"], row["property"])
        for row in _group_count(dataset, property_name)
    ]
    source_ids = np.full(len(flat), -1, dtype=np.int32)
    for source_id, (dataset_value, property_value) in enumerate(source_keys):
        matches = pc.and_(
            _key_match(dataset, dataset_value),
            _key_match(property_name, property_value),
        )
        source_ids[matches.to_numpy(zero_copy_only=False)] = source_id
    if np.any(source_ids < 0):
        raise RuntimeError("failed to encode all multi-source dataset/property keys")

    offsets = multi_lists.offsets.to_numpy(zero_copy_only=False)
    item_counts = np.bincount(source_ids, minlength=len(source_keys))
    first_counts = np.bincount(
        source_ids[offsets[:-1]], minlength=len(source_keys)
    )
    for source_id, source_key in enumerate(source_keys):
        entry = _catalog_entry(catalog, _source_key_json(*source_key))
        entry["first_position_count"] += int(first_counts[source_id])
        entry["later_position_count"] += int(
            item_counts[source_id] - first_counts[source_id]
        )

    combination_counts: Counter[tuple[int, ...]] = Counter()
    feature_counts: Counter[int] = Counter()
    for start, end in zip(offsets[:-1], offsets[1:], strict=True):
        combination = tuple(sorted(source_ids[start:end]))
        combination_counts[combination] += 1
        feature_counts.update(set(combination))
    for source_id, count in feature_counts.items():
        entry = _catalog_entry(
            catalog, _source_key_json(*source_keys[source_id])
        )
        entry["feature_count"] += count
    for combination, count in combination_counts.items():
        value = _combination_json(
            [source_keys[source_id] for source_id in combination]
        )
        combinations[value] = combinations.get(value, 0) + count


def _consume_batch(batch: pa.RecordBatch, partial: dict[str, Any]) -> None:
    sources = batch.column(batch.schema.get_field_index("sources"))
    lengths = pc.list_value_length(sources)
    flat = pc.list_flatten(sources)
    partial["totals"]["feature_count"] += batch.num_rows
    partial["totals"]["source_item_count"] += len(flat)
    partial["totals"]["batch_count"] += 1

    for row in pc.value_counts(lengths).to_pylist():
        key = "null" if row["values"] is None else str(row["values"])
        partial["cardinality"][key] = (
            partial["cardinality"].get(key, 0) + int(row["counts"])
        )

    batch_null_lists = _sum_true(pc.is_null(sources))
    batch_empty_lists = _sum_true(pc.equal(pc.fill_null(lengths, -1), 0))
    partial["anomalies"]["null_source_lists"] += batch_null_lists
    partial["anomalies"]["empty_source_lists"] += batch_empty_lists
    if batch_null_lists:
        partial["combinations"]["null"] = (
            partial["combinations"].get("null", 0) + batch_null_lists
        )
    if batch_empty_lists:
        partial["combinations"]["[]"] = (
            partial["combinations"].get("[]", 0) + batch_empty_lists
        )

    if len(flat) == 0:
        return
    dataset = pc.struct_field(flat, "dataset")
    property_name = pc.struct_field(flat, "property")
    partial["anomalies"]["null_source_items"] += _sum_true(pc.is_null(flat))
    partial["anomalies"]["missing_dataset_items"] += _sum_true(pc.is_null(dataset))
    partial["anomalies"]["missing_property_items"] += _sum_true(
        pc.is_null(property_name)
    )
    _aggregate_flat_sources(flat, partial["catalog"])

    single_lists = pc.filter(sources, pc.equal(lengths, 1))
    single_flat = pc.list_flatten(single_lists)
    if len(single_flat):
        single_dataset = pc.struct_field(single_flat, "dataset")
        single_property = pc.struct_field(single_flat, "property")
        for row in _group_count(single_dataset, single_property):
            count = int(row["count_all"])
            key = _source_key_json(row["dataset"], row["property"])
            entry = _catalog_entry(partial["catalog"], key)
            entry["feature_count"] += count
            entry["first_position_count"] += count
            combination = _combination_json(
                [(row["dataset"], row["property"])]
            )
            partial["combinations"][combination] = (
                partial["combinations"].get(combination, 0) + count
            )

    _aggregate_multi_source_lists(
        pc.filter(sources, pc.greater(lengths, 1)),
        partial["catalog"],
        partial["combinations"],
    )


def _validate_sources_field(field: pa.Field, path: Path) -> None:
    if not pa.types.is_list(field.type):
        raise ValueError(f"{path}: sources must be a list, found {field.type}")
    item_type = field.type.value_type
    if not pa.types.is_struct(item_type):
        raise ValueError(
            f"{path}: sources items must be structs, found {item_type}"
        )
    expected = {
        "property": pa.string(),
        "dataset": pa.string(),
        "license": pa.string(),
        "record_id": pa.string(),
        "update_time": pa.string(),
        "confidence": pa.float64(),
        "between": pa.list_(pa.float64()),
    }
    actual = {item.name: item.type for item in item_type}
    for name, expected_type in expected.items():
        if name not in actual:
            raise ValueError(f"{path}: sources item is missing {name}")
        if actual[name] != expected_type:
            raise ValueError(
                f"{path}: sources.{name} must be {expected_type}, "
                f"found {actual[name]}"
            )


def _inspect_input(
    path: Path,
    source_root: Path,
    *,
    smoke: bool,
) -> dict[str, Any]:
    parquet = pq.ParquetFile(path)
    try:
        field = parquet.schema_arrow.field("sources")
    except KeyError as error:
        raise ValueError(f"{path}: missing sources column") from error
    _validate_sources_field(field, path)
    metadata = parquet.metadata
    scanned_row_groups = min(1, metadata.num_row_groups) if smoke else metadata.num_row_groups
    scanned_rows = sum(
        metadata.row_group(index).num_rows for index in range(scanned_row_groups)
    )
    stat = path.stat()
    field_text = str(field)
    return {
        "path": str(path.relative_to(source_root)),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "parquet_rows": metadata.num_rows,
        "row_groups": metadata.num_row_groups,
        "scanned_rows": scanned_rows,
        "scanned_row_groups": scanned_row_groups,
        "sources_schema": field_text,
        "sources_schema_sha256": hashlib.sha256(field_text.encode()).hexdigest(),
    }


def _new_partial(specification: dict[str, Any], smoke: bool) -> dict[str, Any]:
    return {
        "schema_version": SCANNER_SCHEMA_VERSION,
        "scan_mode": "smoke" if smoke else "full",
        "input": specification,
        "totals": {
            "feature_count": 0,
            "source_item_count": 0,
            "batch_count": 0,
        },
        "catalog": {},
        "combinations": {},
        "cardinality": {},
        "anomalies": {name: 0 for name in _ANOMALY_FIELDS},
    }


def _scan_file(
    path: Path,
    specification: dict[str, Any],
    *,
    batch_size: int,
    smoke: bool,
) -> dict[str, Any]:
    partial = _new_partial(specification, smoke)
    parquet = pq.ParquetFile(path)
    row_groups = [0] if smoke and parquet.metadata.num_row_groups else None
    for batch in parquet.iter_batches(
        batch_size=batch_size,
        row_groups=row_groups,
        columns=["sources"],
        use_threads=True,
    ):
        _consume_batch(batch, partial)
    expected_rows = specification["scanned_rows"]
    actual_rows = partial["totals"]["feature_count"]
    if actual_rows != expected_rows:
        raise RuntimeError(
            f"{path}: scanned {actual_rows} rows, expected {expected_rows}"
        )
    return partial


def _checkpoint_path(checkpoint_dir: Path, relative_path: str) -> Path:
    digest = hashlib.sha256(relative_path.encode()).hexdigest()[:24]
    return checkpoint_dir / f"{digest}.json"


def _load_checkpoint(
    path: Path,
    specification: dict[str, Any],
    *,
    smoke: bool,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if value.get("schema_version") != SCANNER_SCHEMA_VERSION:
        return None
    if value.get("scan_mode") != ("smoke" if smoke else "full"):
        return None
    if value.get("input") != specification:
        return None
    return value


def _merge_partials(partials: list[dict[str, Any]]) -> dict[str, Any]:
    merged = {
        "totals": {
            "feature_count": 0,
            "source_item_count": 0,
            "batch_count": 0,
        },
        "catalog": {},
        "combinations": {},
        "cardinality": {},
        "anomalies": {name: 0 for name in _ANOMALY_FIELDS},
    }
    for partial in partials:
        for name in merged["totals"]:
            merged["totals"][name] += partial["totals"][name]
        for key, incoming in partial["catalog"].items():
            _merge_catalog_entry(
                _catalog_entry(merged["catalog"], key), incoming
            )
        for key, count in partial["combinations"].items():
            merged["combinations"][key] = (
                merged["combinations"].get(key, 0) + count
            )
        for key, count in partial["cardinality"].items():
            merged["cardinality"][key] = (
                merged["cardinality"].get(key, 0) + count
            )
        for name in merged["anomalies"]:
            merged["anomalies"][name] += partial["anomalies"][name]
    return merged


def _validation_checks(
    merged: dict[str, Any],
    *,
    discovered_files: int,
    processed_files: int,
    expected_rows: int,
) -> dict[str, bool]:
    feature_count = merged["totals"]["feature_count"]
    source_item_count = merged["totals"]["source_item_count"]
    cardinality_features = sum(merged["cardinality"].values())
    cardinality_items = sum(
        int(source_count) * count
        for source_count, count in merged["cardinality"].items()
        if source_count != "null"
    )
    checks = {
        "all_files_processed": processed_files == discovered_files,
        "feature_count_matches_parquet_metadata": feature_count == expected_rows,
        "cardinality_feature_count_matches": cardinality_features == feature_count,
        "cardinality_source_item_count_matches": cardinality_items
        == source_item_count,
        "combination_feature_count_matches": sum(
            merged["combinations"].values()
        )
        == feature_count,
        "catalog_source_item_count_matches": sum(
            entry["source_item_count"] for entry in merged["catalog"].values()
        )
        == source_item_count,
    }
    if not all(checks.values()):
        failures = ", ".join(name for name, passed in checks.items() if not passed)
        raise RuntimeError(f"building source validation failed: {failures}")
    return checks


def _csv_text(fieldnames: list[str], rows: list[dict[str, Any]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _catalog_rows(catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for key, entry in sorted(catalog.items()):
        dataset, property_name = json.loads(key)
        if property_name is None:
            scope = "missing"
        elif property_name == "":
            scope = "whole_feature"
        else:
            scope = "field"
        confidence_mean: float | None = None
        if entry["confidence_count"]:
            confidence_mean = (
                entry["confidence_sum"] / entry["confidence_count"]
            )
        rows.append(
            {
                "dataset": dataset,
                "dataset_is_null": dataset is None,
                "property": property_name,
                "property_is_null": property_name is None,
                "property_scope": scope,
                "source_item_count": entry["source_item_count"],
                "feature_count": entry["feature_count"],
                "first_position_count": entry["first_position_count"],
                "later_position_count": entry["later_position_count"],
                "licenses_json": json.dumps(
                    entry["licenses"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "record_id_present_count": entry["record_id_present_count"],
                "update_time_present_count": entry[
                    "update_time_present_count"
                ],
                "update_time_min": entry["update_time_min"],
                "update_time_max": entry["update_time_max"],
                "confidence_count": entry["confidence_count"],
                "confidence_min": entry["confidence_min"],
                "confidence_max": entry["confidence_max"],
                "confidence_mean": confidence_mean,
                "between_present_count": entry["between_present_count"],
            }
        )
    return rows


def _combination_rows(combinations: dict[str, int]) -> list[dict[str, Any]]:
    rows = []
    for value, count in sorted(combinations.items()):
        keys = json.loads(value)
        if keys is None:
            status = "null"
            source_count: int | None = None
        elif not keys:
            status = "empty"
            source_count = 0
        else:
            status = "valid"
            source_count = len(keys)
        rows.append(
            {
                "combination_id": hashlib.sha256(value.encode()).hexdigest(),
                "status": status,
                "source_count": source_count,
                "source_keys_json": value,
                "feature_count": count,
            }
        )
    return rows


def _cardinality_rows(cardinality: dict[str, int]) -> list[dict[str, Any]]:
    def sort_key(item: tuple[str, int]) -> tuple[bool, int]:
        key, _ = item
        return key == "null", -1 if key == "null" else int(key)

    rows = []
    for value, count in sorted(cardinality.items(), key=sort_key):
        rows.append(
            {
                "status": "null" if value == "null" else "valid",
                "source_count": None if value == "null" else int(value),
                "feature_count": count,
            }
        )
    return rows


def _write_outputs(
    output_dir: Path,
    merged: dict[str, Any],
    *,
    config: BenchmarkConfig,
    specifications: list[dict[str, Any]],
    checks: dict[str, bool],
    reused_checkpoints: int,
    smoke: bool,
    duration_seconds: float,
) -> dict[str, Any]:
    catalog_path = output_dir / "source-catalog.csv"
    combination_path = output_dir / "source-combinations.csv"
    cardinality_path = output_dir / "source-cardinality.csv"
    summary_path = output_dir / "summary.json"

    catalog_rows = _catalog_rows(merged["catalog"])
    combination_rows = _combination_rows(merged["combinations"])
    cardinality_rows = _cardinality_rows(merged["cardinality"])
    _atomic_write_text(
        catalog_path,
        _csv_text(
            [
                "dataset",
                "dataset_is_null",
                "property",
                "property_is_null",
                "property_scope",
                "source_item_count",
                "feature_count",
                "first_position_count",
                "later_position_count",
                "licenses_json",
                "record_id_present_count",
                "update_time_present_count",
                "update_time_min",
                "update_time_max",
                "confidence_count",
                "confidence_min",
                "confidence_max",
                "confidence_mean",
                "between_present_count",
            ],
            catalog_rows,
        ),
    )
    _atomic_write_text(
        combination_path,
        _csv_text(
            [
                "combination_id",
                "status",
                "source_count",
                "source_keys_json",
                "feature_count",
            ],
            combination_rows,
        ),
    )
    _atomic_write_text(
        cardinality_path,
        _csv_text(["status", "source_count", "feature_count"], cardinality_rows),
    )

    warnings = [
        f"{name}={count}"
        for name, count in merged["anomalies"].items()
        if count
    ]
    outputs = {}
    for path in (catalog_path, combination_path, cardinality_path):
        outputs[path.name] = {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    fingerprints = sorted(
        {item["sources_schema_sha256"] for item in specifications}
    )
    summary = {
        "schema_version": SCANNER_SCHEMA_VERSION,
        "analysis_id": (
            f"overture-{config.values['source']['overture_release']}"
            "-building-sources-v1"
        ),
        "created_at": utc_now(),
        "complete": not smoke,
        "scan_mode": "smoke" if smoke else "full",
        "source_release": config.values["source"]["overture_release"],
        "source_glob": config.values["source"]["building_glob"],
        "source_identity": ["dataset", "property"],
        "combination_order": "canonical_order_independent",
        "generator": {
            "command": "benchmark.sh analyze-building-sources"
            + (" --smoke" if smoke else ""),
            "config_sha256": config.sha256,
            "git_commit": git_commit(),
            "git_dirty": os.environ.get("BENCHMARK_GIT_DIRTY") == "true",
            "image_digest": os.environ.get("BENCHMARK_IMAGE_ID"),
        },
        "inputs": {
            "file_count": len(specifications),
            "bytes": sum(item["bytes"] for item in specifications),
            "parquet_rows": sum(item["parquet_rows"] for item in specifications),
            "scanned_rows": sum(item["scanned_rows"] for item in specifications),
            "sources_schema_sha256": fingerprints[0],
            "files": specifications,
        },
        "processing": {
            "reused_checkpoint_count": reused_checkpoints,
            "created_checkpoint_count": len(specifications)
            - reused_checkpoints,
            "batch_count": merged["totals"]["batch_count"],
            "duration_seconds": duration_seconds,
        },
        "totals": {
            **merged["totals"],
            "distinct_source_count": len(catalog_rows),
            "distinct_combination_count": len(combination_rows),
        },
        "anomalies": merged["anomalies"],
        "validation": {
            "passed": True,
            "status": "passed_with_warnings" if warnings else "passed",
            "warnings": warnings,
            "checks": checks,
        },
        "outputs": outputs,
        "license": (
            "Overture Maps data; see "
            "https://docs.overturemaps.org/attribution/"
        ),
    }
    _atomic_write_json(summary_path, summary)
    return {
        "output_dir": str(output_dir),
        "summary_path": str(summary_path),
        "complete": summary["complete"],
        "file_count": len(specifications),
        "feature_count": merged["totals"]["feature_count"],
        "source_item_count": merged["totals"]["source_item_count"],
        "distinct_source_count": len(catalog_rows),
        "distinct_combination_count": len(combination_rows),
        "reused_checkpoint_count": reused_checkpoints,
        "warnings": warnings,
    }


def analyze_building_sources(
    config: BenchmarkConfig,
    *,
    workers: int = DEFAULT_WORKERS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    rebuild: bool = False,
    smoke: bool = False,
) -> dict[str, Any]:
    """Scan configured Overture building files and write source aggregates."""
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    started = time.perf_counter()
    files = config.source_glob("building_glob")
    if smoke:
        files = files[:1]
    specifications = [
        _inspect_input(path, config.source_root, smoke=smoke) for path in files
    ]
    fingerprints = {
        specification["sources_schema_sha256"]
        for specification in specifications
    }
    if len(fingerprints) != 1:
        raise ValueError(
            "building files contain inconsistent sources schemas: "
            + ", ".join(sorted(fingerprints))
        )

    release = config.values["source"]["overture_release"]
    output_dir = (
        config.output_root / "analysis" / "building-sources" / str(release)
    )
    if smoke:
        output_dir = output_dir / "smoke"
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    partials_by_path: dict[str, dict[str, Any]] = {}
    jobs: list[tuple[Path, dict[str, Any], Path]] = []
    reused_checkpoints = 0
    for path, specification in zip(files, specifications, strict=True):
        checkpoint = _checkpoint_path(checkpoint_dir, specification["path"])
        partial = None
        if not rebuild:
            partial = _load_checkpoint(
                checkpoint, specification, smoke=smoke
            )
        if partial is not None:
            partials_by_path[specification["path"]] = partial
            reused_checkpoints += 1
            print(
                f"Reused building source checkpoint: {specification['path']}",
                file=sys.stderr,
                flush=True,
            )
        else:
            jobs.append((path, specification, checkpoint))

    def finish_job(
        path: Path,
        specification: dict[str, Any],
        checkpoint: Path,
    ) -> tuple[str, dict[str, Any]]:
        partial = _scan_file(
            path,
            specification,
            batch_size=batch_size,
            smoke=smoke,
        )
        _atomic_write_json(checkpoint, partial)
        return specification["path"], partial

    if workers == 1:
        for path, specification, checkpoint in jobs:
            relative_path, partial = finish_job(
                path, specification, checkpoint
            )
            partials_by_path[relative_path] = partial
            print(
                f"Scanned building sources: {relative_path}",
                file=sys.stderr,
                flush=True,
            )
    elif jobs:
        with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as executor:
            futures = {
                executor.submit(
                    finish_job, path, specification, checkpoint
                ): specification["path"]
                for path, specification, checkpoint in jobs
            }
            for future in as_completed(futures):
                relative_path, partial = future.result()
                partials_by_path[relative_path] = partial
                print(
                    f"Scanned building sources: {relative_path}",
                    file=sys.stderr,
                    flush=True,
                )

    ordered_partials = [
        partials_by_path[specification["path"]]
        for specification in specifications
    ]
    merged = _merge_partials(ordered_partials)
    checks = _validation_checks(
        merged,
        discovered_files=len(specifications),
        processed_files=len(ordered_partials),
        expected_rows=sum(
            specification["scanned_rows"]
            for specification in specifications
        ),
    )
    return _write_outputs(
        output_dir,
        merged,
        config=config,
        specifications=specifications,
        checks=checks,
        reused_checkpoints=reused_checkpoints,
        smoke=smoke,
        duration_seconds=time.perf_counter() - started,
    )
