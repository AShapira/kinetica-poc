from pathlib import Path

import pytest

from sedona_benchmark.config import load_config


def test_required_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("MISSING_VALUE", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("schema_version: 1\nvalue: '${MISSING_VALUE}'\n")
    with pytest.raises(ValueError, match="MISSING_VALUE"):
        load_config(path)


def test_default_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("OPTIONAL_VALUE", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("schema_version: 1\nvalue: '${OPTIONAL_VALUE:-fallback}'\n")
    assert load_config(path).values["value"] == "fallback"
