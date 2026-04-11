#!/usr/bin/env python
"""Generate JSON Schema files for all gitventory inventory YAML files.

Run from the repo root:
    python scripts/generate_schemas.py

Schemas are written to schemas/.  Re-run this script whenever the inventory
models change (gitventory/adapters/static_yaml/schema.py, catalog/schema.py,
or config.py).

Editor integration
------------------
VS Code with the Red Hat YAML extension picks up schemas via two mechanisms:

1. The yaml-language-server comment at the top of each YAML file:
       # yaml-language-server: $schema=../schemas/teams.schema.json

2. The .vscode/settings.json mapping (generated alongside the schemas):
       "yaml.schemas": {
           "./schemas/teams.schema.json": ["inventory/teams.yaml", "inventory/teams.*.yaml"]
       }

Both are written by this script.  Mechanism 2 requires no per-file annotation,
so it's preferred for files you didn't author yourself.

JetBrains IDEs (IntelliJ, PyCharm, …) honour the $schema comment natively.
"""

from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Schema sources
# ---------------------------------------------------------------------------

SCHEMAS: list[tuple[str, object, str]] = []

try:
    from gitventory.adapters.static_yaml.schema import TeamsFile
    SCHEMAS.append((
        "teams",
        TeamsFile,
        "Schema for inventory/teams.yaml — org parties (teams, squads, chapters, guilds).",
    ))
except ImportError as e:
    print(f"  skipping teams: {e}")

try:
    from gitventory.adapters.static_yaml.schema import AwsAccountsFile
    SCHEMAS.append((
        "aws_accounts",
        AwsAccountsFile,
        "Schema for inventory/aws_accounts.yaml — known AWS accounts.",
    ))
except ImportError as e:
    print(f"  skipping aws_accounts: {e}")

try:
    from gitventory.adapters.static_yaml.schema import DeploymentMappingsFile
    SCHEMAS.append((
        "deployment_mappings",
        DeploymentMappingsFile,
        "Schema for inventory/deployment_mappings.yaml — manual repo→cloud account mappings.",
    ))
except ImportError as e:
    print(f"  skipping deployment_mappings: {e}")

try:
    from gitventory.catalog.schema import CatalogFile
    SCHEMAS.append((
        "catalog",
        CatalogFile,
        "Schema for inventory/catalog.yaml — organizational meta-model (services, projects, …).",
    ))
except ImportError as e:
    print(f"  skipping catalog: {e}")

try:
    from gitventory.config import AppConfig
    SCHEMAS.append((
        "config",
        AppConfig,
        "Schema for config.yaml — gitventory configuration.",
    ))
except ImportError as e:
    print(f"  skipping config: {e}")


# ---------------------------------------------------------------------------
# VS Code settings mapping
# ---------------------------------------------------------------------------

# Maps schema filename → glob patterns for files that should use it.
# Relative to the workspace root.
_VSCODE_YAML_SCHEMAS: dict[str, list[str]] = {
    "./schemas/teams.schema.json":              ["inventory/teams.yaml", "inventory/teams.*.yaml"],
    "./schemas/aws_accounts.schema.json":       ["inventory/aws_accounts.yaml", "inventory/aws_accounts.*.yaml"],
    "./schemas/deployment_mappings.schema.json":["inventory/deployment_mappings.yaml"],
    "./schemas/catalog.schema.json":            ["inventory/catalog.yaml", "inventory/catalog.example.yaml"],
    "./schemas/config.schema.json":             ["config.yaml", "config.*.yaml"],
}


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _enrich(schema: dict, description: str) -> dict:
    """Add $schema meta-reference and top-level description."""
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    if "description" not in schema:
        schema["description"] = description
    return schema


def main() -> None:
    repo_root = Path(__file__).parent.parent
    out_dir = repo_root / "schemas"
    out_dir.mkdir(exist_ok=True)

    written: list[str] = []
    for name, model_cls, description in SCHEMAS:
        schema = model_cls.model_json_schema()  # type: ignore[attr-defined]
        schema = _enrich(schema, description)
        out_path = out_dir / f"{name}.schema.json"
        out_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")
        print(f"  wrote {out_path.relative_to(repo_root)}")
        written.append(name)

    # Write / update .vscode/settings.json
    vscode_dir = repo_root / ".vscode"
    vscode_dir.mkdir(exist_ok=True)
    settings_path = vscode_dir / "settings.json"

    # Merge with existing settings if present
    existing: dict = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass  # Overwrite corrupt/empty file

    existing.setdefault("yaml.schemas", {}).update(_VSCODE_YAML_SCHEMAS)
    settings_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"  wrote {settings_path.relative_to(repo_root)}")

    print(f"\nDone — {len(written)} schema(s) written to schemas/")


if __name__ == "__main__":
    main()
