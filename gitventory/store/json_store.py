"""Flat JSON store — for development and testing only, not recommended for production."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Optional, Type

from gitventory.models.base import InventoryEntity
from gitventory.models.cloud_account import CloudAccount
from gitventory.models.deployment_mapping import DeploymentMapping
from gitventory.models.ghas_alert import GhasAlert
from gitventory.models.repository import Repository
from gitventory.models.team import Team
from gitventory.store.base import AbstractStore

logger = logging.getLogger(__name__)

E = Type[InventoryEntity]

_TYPE_FILE: dict[type, str] = {
    Repository: "repositories.json",
    CloudAccount: "cloud_accounts.json",
    Team: "teams.json",
    DeploymentMapping: "deployment_mappings.json",
    GhasAlert: "ghas_alerts.json",
}


class FlatJsonStore(AbstractStore):
    """One JSON file per entity type in a directory.  Not suitable for large datasets."""

    def __init__(self, directory: str) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._runs_file = self._dir / "collection_runs.json"

    def init_schema(self) -> None:
        pass  # Nothing to initialise for flat files

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert(self, entity: InventoryEntity) -> None:
        data = self._load(type(entity))
        data[entity.id] = json.loads(entity.model_dump_json())
        self._save(type(entity), data)

    def upsert_many(self, entities: Iterator[InventoryEntity]) -> int:
        batches: dict[type, dict[str, Any]] = {}
        count = 0
        for entity in entities:
            et = type(entity)
            if et not in batches:
                batches[et] = self._load(et)
            batches[et][entity.id] = json.loads(entity.model_dump_json())
            count += 1
        for et, data in batches.items():
            self._save(et, data)
        return count

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, entity_type: Type[E], entity_id: str) -> Optional[E]:
        data = self._load(entity_type)
        row = data.get(entity_id)
        if row is None:
            return None
        return entity_type(**row)

    def query(self, entity_type: Type[E], filters: dict[str, Any]) -> List[E]:
        data = self._load(entity_type)
        results = []
        for row in data.values():
            obj = entity_type(**row)
            if _matches(obj, filters):
                results.append(obj)
        return results

    def count(self, entity_type: Type[E]) -> int:
        return len(self._load(entity_type))

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    def last_collected_at(self, adapter_name: str) -> Optional[datetime]:
        runs = self._load_runs()
        for run in reversed(runs):
            if run.get("adapter_name") == adapter_name and run.get("status") == "success":
                ts = run.get("finished_at")
                return datetime.fromisoformat(ts) if ts else None
        return None

    def record_collection_run(
        self,
        adapter_name: str,
        started_at: datetime,
        finished_at: datetime,
        entity_count: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        runs = self._load_runs()
        runs.append({
            "adapter_name": adapter_name,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "entity_count": entity_count,
            "status": status,
            "error_message": error_message,
        })
        self._runs_file.write_text(json.dumps(runs, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self, entity_type: type) -> dict[str, Any]:
        path = self._dir / _TYPE_FILE[entity_type]
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, entity_type: type, data: dict[str, Any]) -> None:
        path = self._dir / _TYPE_FILE[entity_type]
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def _load_runs(self) -> list[dict]:
        if not self._runs_file.exists():
            return []
        return json.loads(self._runs_file.read_text(encoding="utf-8"))


def _matches(obj: InventoryEntity, filters: dict[str, Any]) -> bool:
    """Naive in-memory filter matching for the JSON store."""
    for key, value in filters.items():
        if key == "has_open_alerts" and value:
            repo = obj  # only makes sense for Repository
            if not (
                getattr(repo, "open_secret_alerts", 0) > 0
                or getattr(repo, "open_code_scanning_alerts", 0) > 0
                or getattr(repo, "open_dependabot_alerts", 0) > 0
            ):
                return False
            continue

        for suffix, op in [("__gt", ">"), ("__lt", "<"), ("__gte", ">="), ("__lte", "<=")]:
            if key.endswith(suffix):
                field = key[: -len(suffix)]
                attr = getattr(obj, field, None)
                if attr is None:
                    return False
                if op == ">" and not (attr > value):
                    return False
                if op == "<" and not (attr < value):
                    return False
                if op == ">=" and not (attr >= value):
                    return False
                if op == "<=" and not (attr <= value):
                    return False
                break
        else:
            attr = getattr(obj, key, None)
            if attr != value:
                return False

    return True
