"""JSON output formatter."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from gitventory.output.base import AbstractFormatter


class JsonFormatter(AbstractFormatter):

    def format(self, data: list[Any], fields: list[str], title: str) -> str:
        rows = []
        for obj in data:
            row = {}
            for field in fields:
                val = getattr(obj, field, None)
                row[field] = val
            rows.append(row)
        return json.dumps(rows, indent=2, default=_json_default)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serialisable")
