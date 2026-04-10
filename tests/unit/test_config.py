"""Unit tests for config loading."""

import os
from pathlib import Path

import pytest

from gitventory.config import AppConfig, _interpolate_env_vars, load_config


def test_interpolate_env_vars_replaces_known(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "secret123")
    result = _interpolate_env_vars("token: ${MY_TOKEN}")
    assert result == "token: secret123"


def test_interpolate_env_vars_empty_string_on_missing():
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = _interpolate_env_vars("token: ${MISSING_VAR_XYZ_GITVENTORY}")
    assert result == "token: "
    assert any("MISSING_VAR_XYZ_GITVENTORY" in str(warning.message) for warning in w)


def test_interpolate_env_vars_default_syntax():
    result = _interpolate_env_vars("token: ${NOT_SET_VAR:-fallback_value}")
    assert result == "token: fallback_value"


def test_load_config_from_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_test")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        """
version: "1"
store:
  backend: sqlite
  sqlite:
    path: "./data/test.db"
adapters:
  github:
    enabled: true
    token: "${GH_TOKEN}"
    orgs:
      - test-org
  static_yaml:
    enabled: true
    teams_file: "./inventory/teams.yaml"
"""
    )
    config = load_config(cfg_file)
    assert config.version == "1"
    assert config.store.backend == "sqlite"
    assert config.adapters.github is not None
    assert config.adapters.github.token == "ghp_test"
    assert config.adapters.github.orgs == ["test-org"]
    assert config.adapters.static_yaml is not None


def test_load_config_file_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nonexistent.yaml")


def test_default_config():
    cfg = AppConfig()
    assert cfg.store.backend == "sqlite"
    assert cfg.logging.level == "INFO"
    assert cfg.output.default_format == "table"
