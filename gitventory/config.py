"""Configuration loading and validation for gitventory."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, field_validator, model_validator

# ---------------------------------------------------------------------------
# Store config
# ---------------------------------------------------------------------------

class SqliteStoreConfig(BaseModel):
    path: str = "./data/gitventory.db"


class JsonStoreConfig(BaseModel):
    directory: str = "./data"


class PostgresStoreConfig(BaseModel):
    url: str


class StoreConfig(BaseModel):
    backend: str = "sqlite"
    sqlite: SqliteStoreConfig = SqliteStoreConfig()
    json_store: JsonStoreConfig = JsonStoreConfig()
    postgres: Optional[PostgresStoreConfig] = None

    @field_validator("backend")
    @classmethod
    def valid_backend(cls, v: str) -> str:
        allowed = {"sqlite", "json", "postgres"}
        if v not in allowed:
            raise ValueError(f"store.backend must be one of {allowed}, got {v!r}")
        return v


# ---------------------------------------------------------------------------
# Adapter configs
# ---------------------------------------------------------------------------

class AdapterConfig(BaseModel):
    enabled: bool = True


class GitHubAdapterConfig(AdapterConfig):
    token: str = ""
    orgs: list[str] = []
    include_archived: bool = False
    collect_ghas_alerts: bool = True
    collect_secret_scanning: bool = True
    collect_dependabot: bool = True
    parse_workflows: bool = True
    rate_limit_sleep_seconds: float = 1.0
    per_page: int = 100


class StaticYamlAdapterConfig(AdapterConfig):
    teams_file: Optional[str] = None
    aws_accounts_file: Optional[str] = None
    deployment_mappings_file: Optional[str] = None


class AdaptersConfig(BaseModel):
    github: Optional[GitHubAdapterConfig] = None
    static_yaml: Optional[StaticYamlAdapterConfig] = None

    def enabled_adapters(self) -> dict[str, AdapterConfig]:
        result = {}
        if self.github and self.github.enabled:
            result["github"] = self.github
        if self.static_yaml and self.static_yaml.enabled:
            result["static_yaml"] = self.static_yaml
        return result


# ---------------------------------------------------------------------------
# Logging / output config
# ---------------------------------------------------------------------------

class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "text"


class OutputConfig(BaseModel):
    default_format: str = "table"
    date_format: str = "%Y-%m-%d"


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------

class AppConfig(BaseModel):
    version: str = "1"
    store: StoreConfig = StoreConfig()
    adapters: AdaptersConfig = AdaptersConfig()
    logging: LoggingConfig = LoggingConfig()
    output: OutputConfig = OutputConfig()

    @model_validator(mode="after")
    def warn_unknown_version(self) -> "AppConfig":
        if self.version != "1":
            import warnings
            warnings.warn(
                f"config version {self.version!r} is not recognised (expected '1'). "
                "Some settings may be ignored.",
                stacklevel=2,
            )
        return self


# ---------------------------------------------------------------------------
# Env-var interpolation
# ---------------------------------------------------------------------------

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _interpolate_env_vars(text: str) -> str:
    """Replace ``${VAR_NAME}`` references with environment variable values.

    Supports optional defaults: ``${VAR_NAME:-default_value}``.
    If a variable is not set and has no default, substitutes an empty string
    and logs a warning.  Adapters are responsible for failing fast via
    ``validate_connectivity()`` if they require a value that ends up empty.
    """
    import warnings

    def replace(match: re.Match) -> str:
        expr = match.group(1)
        # Support ${VAR:-default} syntax
        if ":-" in expr:
            name, default = expr.split(":-", 1)
        else:
            name, default = expr, None

        value = os.environ.get(name)
        if value is not None:
            return value
        if default is not None:
            return default
        warnings.warn(
            f"Config references ${{{name}}} but the environment variable is not set "
            f"— substituting empty string. Set {name} before running this adapter.",
            stacklevel=4,
        )
        return ""

    return _ENV_VAR_RE.sub(replace, text)


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

def load_config(path: str | Path) -> AppConfig:
    """Load a YAML config file, interpolate ``${ENV_VAR}`` references, and validate."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw_text = config_path.read_text(encoding="utf-8")
    interpolated = _interpolate_env_vars(raw_text)
    data: dict[str, Any] = yaml.safe_load(interpolated) or {}
    return AppConfig(**data)
