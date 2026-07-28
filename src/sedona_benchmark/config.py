"""Configuration loading with explicit environment substitution."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_ENV = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::-(.*?))?\}")


def _expand(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        current = os.environ.get(name)
        if current:
            return current
        if default is not None:
            return default
        raise ValueError(f"required environment variable {name} is not set")

    return _ENV.sub(replace, value)


@dataclass(frozen=True)
class BenchmarkConfig:
    path: Path
    raw_text: str
    values: dict[str, Any]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw_text.encode()).hexdigest()

    @property
    def source_root(self) -> Path:
        return Path(self.values["source"]["root"])

    @property
    def output_root(self) -> Path:
        return Path(self.values["output"]["root"])

    def source_glob(self, key: str) -> list[Path]:
        files = sorted(self.source_root.glob(self.values["source"][key]))
        if not files:
            raise FileNotFoundError(f"no files match source.{key}")
        return files


def load_config(path: str | Path) -> BenchmarkConfig:
    config_path = Path(path).resolve()
    raw = config_path.read_text(encoding="utf-8")
    expanded = _expand(raw)
    values = yaml.safe_load(expanded)
    if values.get("schema_version") != 1:
        raise ValueError("benchmark config schema_version must be 1")
    return BenchmarkConfig(config_path, raw, values)
