"""Shared rendering primitives — console instance, generic table/JSON output."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()

# Columns that trigger colour formatting
_ALERT_COLS = {"open_secret_alerts", "open_code_scanning_alerts", "open_dependabot_alerts"}


def output(results: list, cols: list[str], fmt: str, title: str) -> None:
    """Render a list of entities as a Rich table or a JSON array."""
    if fmt == "json":
        output_data = []
        for obj in results:
            row: dict[str, Any] = {}
            for col in cols:
                val = getattr(obj, col, None)
                row[col] = str(val) if val is not None else None
            output_data.append(row)
        console.print_json(json.dumps(output_data))
        return

    table = Table(title=f"{title} ({len(results)})")
    for col in cols:
        table.add_column(col.replace("_", " ").title())

    for obj in results:
        row_vals = []
        for col in cols:
            val = getattr(obj, col, None)
            if val is None:
                row_vals.append("[dim]—[/dim]")
            elif col == "is_archived" and val:
                row_vals.append("[yellow]archived[/yellow]")
            elif col in _ALERT_COLS and val > 0:
                row_vals.append(f"[red]{val}[/red]")
            else:
                row_vals.append(str(val))
        table.add_row(*row_vals)

    console.print(table)


def print_detail(entity: Any) -> None:
    """Print all fields of an entity as a two-column key/value table."""
    from rich.table import Table as _Table  # noqa: F811 — avoid shadowing

    table = _Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold dim")
    table.add_column("Value")

    for field_name, value in entity.model_dump().items():
        if field_name == "raw":
            continue
        display = json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value)
        table.add_row(field_name, display)

    console.print(table)
