"""SQLite store implementation using SQLAlchemy Core."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Optional, Type

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
)
from sqlalchemy.engine import Engine

from gitventory.models.base import InventoryEntity
from gitventory.models.cloud_account import CloudAccount
from gitventory.models.deployment_mapping import DeploymentMapping
from gitventory.models.ghas_alert import GhasAlert
from gitventory.models.repository import Repository
from gitventory.models.team import Team
from gitventory.store.base import AbstractStore

logger = logging.getLogger(__name__)

E = Type[InventoryEntity]

# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------

metadata = MetaData()

repositories = Table(
    "repositories",
    metadata,
    Column("id", String, primary_key=True),
    Column("provider_id", String, nullable=False),
    Column("provider", String, nullable=False),
    Column("source_adapter", String, nullable=False),
    Column("collected_at", DateTime(timezone=True), nullable=False),
    Column("org", String),
    Column("project", String),
    Column("name", String),
    Column("full_name", String),
    Column("url", String),
    Column("language", String),
    Column("topics", Text),           # JSON array
    Column("visibility", String),
    Column("is_archived", Boolean),
    Column("is_fork", Boolean),
    Column("is_template", Boolean),
    Column("default_branch", String),
    Column("last_push_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)),
    Column("ghas_enabled", Boolean),
    Column("open_secret_alerts", Integer),
    Column("open_code_scanning_alerts", Integer),
    Column("open_dependabot_alerts", Integer),
    Column("owning_team_id", String),
    Column("raw", Text),              # JSON blob
)

cloud_accounts = Table(
    "cloud_accounts",
    metadata,
    Column("id", String, primary_key=True),
    Column("provider_id", String, nullable=False),
    Column("provider", String, nullable=False),
    Column("source_adapter", String, nullable=False),
    Column("collected_at", DateTime(timezone=True), nullable=False),
    Column("name", String),
    Column("environment", String),
    Column("ou_path", String),
    Column("owning_team_id", String),
    Column("tags", Text),             # JSON object
    Column("raw", Text),
)

teams = Table(
    "teams",
    metadata,
    Column("id", String, primary_key=True),
    Column("provider_id", String, nullable=False),
    Column("source_adapter", String, nullable=False),
    Column("collected_at", DateTime(timezone=True), nullable=False),
    Column("display_name", String),
    Column("email", String),
    Column("slack_channel", String),
    Column("github_team_slug", String),
    Column("members", Text),          # JSON array
    Column("raw", Text),
)

deployment_mappings = Table(
    "deployment_mappings",
    metadata,
    Column("id", String, primary_key=True),
    Column("provider_id", String, nullable=False),
    Column("source_adapter", String, nullable=False),
    Column("collected_at", DateTime(timezone=True), nullable=False),
    Column("repo_id", String),
    Column("target_type", String),
    Column("target_id", String),
    Column("deploy_method", String),
    Column("environment", String),
    Column("detection_method", String),
    Column("notes", Text),
    Column("raw", Text),
)

ghas_alerts = Table(
    "ghas_alerts",
    metadata,
    Column("id", String, primary_key=True),
    Column("provider_id", String, nullable=False),
    Column("source_adapter", String, nullable=False),
    Column("collected_at", DateTime(timezone=True), nullable=False),
    Column("repo_id", String),
    Column("alert_type", String),
    Column("number", Integer),
    Column("state", String),
    Column("severity", String),
    Column("rule_id", String),
    Column("secret_type", String),
    Column("secret_type_display_name", String),
    Column("created_at", DateTime(timezone=True)),
    Column("dismissed_at", DateTime(timezone=True)),
    Column("dismissed_reason", String),
    Column("fixed_at", DateTime(timezone=True)),
    Column("url", String),
    Column("raw", Text),
)

collection_runs = Table(
    "collection_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("adapter_name", String, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("entity_count", Integer),
    Column("status", String),         # "success" | "partial" | "failed"
    Column("error_message", Text),
)

# Map entity type → (table, entity class)
_TYPE_TABLE: dict[type, Table] = {
    Repository: repositories,
    CloudAccount: cloud_accounts,
    Team: teams,
    DeploymentMapping: deployment_mappings,
    GhasAlert: ghas_alerts,
}

# ---------------------------------------------------------------------------
# Indexes (created alongside tables)
# ---------------------------------------------------------------------------

_INDEXES = [
    sa.Index("ix_repos_org", repositories.c.org),
    sa.Index("ix_repos_provider", repositories.c.provider),
    sa.Index("ix_repos_owning_team", repositories.c.owning_team_id),
    sa.Index("ix_repos_last_push", repositories.c.last_push_at),
    sa.Index("ix_repos_archived", repositories.c.is_archived),
    sa.Index("ix_accounts_provider", cloud_accounts.c.provider),
    sa.Index("ix_accounts_env", cloud_accounts.c.environment),
    sa.Index("ix_accounts_team", cloud_accounts.c.owning_team_id),
    sa.Index("ix_alerts_repo", ghas_alerts.c.repo_id),
    sa.Index("ix_alerts_state", ghas_alerts.c.state),
    sa.Index("ix_alerts_type", ghas_alerts.c.alert_type),
    sa.Index("ix_mappings_repo", deployment_mappings.c.repo_id),
    sa.Index("ix_mappings_target", deployment_mappings.c.target_id),
    sa.Index("ix_mappings_method", deployment_mappings.c.detection_method),
]


# ---------------------------------------------------------------------------
# SQLiteStore
# ---------------------------------------------------------------------------

class SQLiteStore(AbstractStore):
    """SQLAlchemy Core store backed by SQLite.

    Uses the same table definitions and query logic that a future PostgresStore
    will reuse — the only difference is the engine URL.
    """

    def __init__(self, path: str) -> None:
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine: Engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        self.init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_schema(self) -> None:
        metadata.create_all(self._engine)
        # Create indexes — SQLAlchemy silently skips existing ones
        with self._engine.connect() as conn:
            for idx in _INDEXES:
                try:
                    idx.create(conn, checkfirst=True)
                except Exception:
                    pass  # Index may already exist

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert(self, entity: InventoryEntity) -> None:
        table = _TYPE_TABLE[type(entity)]
        row = _entity_to_row(entity)
        with self._engine.begin() as conn:
            _upsert_row(conn, table, row)

    def upsert_many(self, entities: Iterator[InventoryEntity]) -> int:
        count = 0
        with self._engine.begin() as conn:
            for entity in entities:
                table = _TYPE_TABLE[type(entity)]
                row = _entity_to_row(entity)
                _upsert_row(conn, table, row)
                count += 1
        return count

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, entity_type: Type[E], entity_id: str) -> Optional[E]:
        table = _TYPE_TABLE[entity_type]
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(table).where(table.c.id == entity_id)
            ).mappings().first()
        if row is None:
            return None
        return _row_to_entity(entity_type, dict(row))

    def query(self, entity_type: Type[E], filters: dict[str, Any]) -> List[E]:
        table = _TYPE_TABLE[entity_type]
        stmt = sa.select(table)
        stmt = _apply_filters(stmt, table, filters)

        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()

        return [_row_to_entity(entity_type, dict(r)) for r in rows]

    def count(self, entity_type: Type[E]) -> int:
        table = _TYPE_TABLE[entity_type]
        with self._engine.connect() as conn:
            result = conn.execute(sa.select(sa.func.count()).select_from(table)).scalar()
        return result or 0

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    def last_collected_at(self, adapter_name: str) -> Optional[datetime]:
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(collection_runs.c.finished_at)
                .where(collection_runs.c.adapter_name == adapter_name)
                .where(collection_runs.c.status == "success")
                .order_by(collection_runs.c.finished_at.desc())
                .limit(1)
            ).scalar()
        return row

    def record_collection_run(
        self,
        adapter_name: str,
        started_at: datetime,
        finished_at: datetime,
        entity_count: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                collection_runs.insert().values(
                    adapter_name=adapter_name,
                    started_at=started_at,
                    finished_at=finished_at,
                    entity_count=entity_count,
                    status=status,
                    error_message=error_message,
                )
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._engine.dispose()

    # ------------------------------------------------------------------
    # Extra: store status summary
    # ------------------------------------------------------------------

    def status_summary(self) -> dict[str, Any]:
        """Return entity counts and last collection times per adapter."""
        summary: dict[str, Any] = {"entity_counts": {}, "last_collected": {}}
        with self._engine.connect() as conn:
            for entity_type, table in _TYPE_TABLE.items():
                n = conn.execute(sa.select(sa.func.count()).select_from(table)).scalar() or 0
                summary["entity_counts"][entity_type.__name__] = n

            rows = conn.execute(
                sa.select(
                    collection_runs.c.adapter_name,
                    sa.func.max(collection_runs.c.finished_at).label("last_run"),
                )
                .where(collection_runs.c.status == "success")
                .group_by(collection_runs.c.adapter_name)
            ).all()
            for row in rows:
                summary["last_collected"][row[0]] = row[1]

        return summary

    def export_all(self) -> dict[str, list[dict]]:
        """Dump all entities as a dict of lists for JSON export."""
        result: dict[str, list[dict]] = {}
        with self._engine.connect() as conn:
            for entity_type, table in _TYPE_TABLE.items():
                rows = conn.execute(sa.select(table)).mappings().all()
                result[entity_type.__name__] = [dict(r) for r in rows]
        return result


# ---------------------------------------------------------------------------
# Row ↔ Entity conversion helpers
# ---------------------------------------------------------------------------

def _entity_to_row(entity: InventoryEntity) -> dict[str, Any]:
    """Convert an InventoryEntity to a flat dict suitable for SQL insertion."""
    data = entity.model_dump(mode="python")
    # Serialise nested structures to JSON text
    for key in ("topics", "members", "tags"):
        if key in data and data[key] is not None:
            data[key] = json.dumps(data[key])
    if "raw" in data and data["raw"] is not None:
        data["raw"] = json.dumps(data["raw"])
    return data


def _row_to_entity(entity_type: Type[E], row: dict[str, Any]) -> E:
    """Reconstruct an InventoryEntity from a SQL row dict."""
    data = dict(row)
    for key in ("topics", "members", "tags"):
        if key in data and data[key] is not None:
            data[key] = json.loads(data[key])
    if "raw" in data and data["raw"] is not None:
        data["raw"] = json.loads(data["raw"]) if data["raw"] else None
    return entity_type(**data)


def _upsert_row(conn: Any, table: Table, row: dict[str, Any]) -> None:
    """Idempotent upsert by primary key using INSERT ... ON CONFLICT DO UPDATE."""
    stmt = sa.dialects.sqlite.insert(table).values(**row)
    # Update every column except the primary key on conflict
    update_cols = {k: v for k, v in row.items() if k != "id"}
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
    conn.execute(stmt)


# ---------------------------------------------------------------------------
# Filter application
# ---------------------------------------------------------------------------

_OPERATOR_SUFFIXES = {
    "__gt": lambda col, val: col > val,
    "__lt": lambda col, val: col < val,
    "__gte": lambda col, val: col >= val,
    "__lte": lambda col, val: col <= val,
    "__contains": lambda col, val: col.contains(val),
    "__isnull": lambda col, val: col.is_(None) if val else col.isnot(None),
}


def _apply_filters(stmt: Any, table: Table, filters: dict[str, Any]) -> Any:
    for key, value in filters.items():
        # Synthetic filter: has_open_alerts (no direct column)
        if key == "has_open_alerts" and value:
            stmt = stmt.where(
                (table.c.open_secret_alerts > 0)
                | (table.c.open_code_scanning_alerts > 0)
                | (table.c.open_dependabot_alerts > 0)
            )
            continue

        # Check operator suffixes
        matched = False
        for suffix, op_fn in _OPERATOR_SUFFIXES.items():
            if key.endswith(suffix):
                field = key[: -len(suffix)]
                if hasattr(table.c, field):
                    stmt = stmt.where(op_fn(getattr(table.c, field), value))
                    matched = True
                    break
        if matched:
            continue

        # Equality
        if hasattr(table.c, key):
            stmt = stmt.where(getattr(table.c, key) == value)

    return stmt
