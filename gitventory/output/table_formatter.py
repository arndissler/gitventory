"""Rich-based terminal table formatter."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table

from gitventory.output.base import AbstractFormatter

# Columns that get coloured red when their value is > 0
_ALERT_COLS = {
    "open_secret_alerts",
    "open_code_scanning_alerts",
    "open_dependabot_alerts",
}


class TableFormatter(AbstractFormatter):

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    def format(self, data: list[Any], fields: list[str], title: str) -> str:
        """Render as a Rich table and return the string (for capture / testing)."""
        table = _build_table(data, fields, title)
        with self._console.capture() as capture:
            self._console.print(table)
        return capture.get()

    def print(self, data: list[Any], fields: list[str], title: str) -> None:
        """Render and print directly to the console."""
        table = _build_table(data, fields, title)
        self._console.print(table)


def _build_table(data: list[Any], fields: list[str], title: str) -> Table:
    table = Table(title=f"{title} ({len(data)})")
    for col in fields:
        table.add_column(col.replace("_", " ").title())

    for obj in data:
        row_vals = []
        for col in fields:
            val = getattr(obj, col, None)
            row_vals.append(_cell(col, val))
        table.add_row(*row_vals)

    return table


def _cell(col: str, val: Any) -> str:
    if val is None:
        return "[dim]—[/dim]"
    if col == "is_archived" and val:
        return "[yellow]archived[/yellow]"
    if col in _ALERT_COLS and isinstance(val, int) and val > 0:
        return f"[red]{val}[/red]"
    return str(val)
