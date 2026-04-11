"""CatalogSyncer — loads the catalog YAML and materialises CatalogMembership records."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from gitventory.catalog.matcher import CatalogMatcher
from gitventory.catalog.schema import CatalogFile, CatalogEntityEntry
from gitventory.models.catalog import CatalogEntity, CatalogMembership
from gitventory.store.base import AbstractStore

logger = logging.getLogger(__name__)


class CatalogSyncer:
    """Orchestrates a catalog sync run.

    1. Loads and validates the catalog YAML.
    2. Optionally clears existing memberships (``clear=True``).
    3. Upserts ``CatalogEntity`` records.
    4. Evaluates matchers against the store and upserts ``CatalogMembership`` records.

    The store is used for both reading (finding repos/accounts to match) and
    writing (persisting entities and memberships).
    """

    SOURCE_ADAPTER = "catalog_yaml"

    def __init__(self, catalog_path: str, store: AbstractStore) -> None:
        self._path = Path(catalog_path)
        self._store = store

    def sync(self, clear: bool = False) -> dict[str, int]:
        """Run a full catalog sync.

        Parameters
        ----------
        clear:
            If True, all existing CatalogMembership records are deleted before
            the matchers are evaluated.  Use this to remove stale links after
            renaming or removing entities or matcher rules.

        Returns
        -------
        dict with keys ``"entities"`` and ``"memberships"``.
        """
        if not self._path.exists():
            raise FileNotFoundError(f"Catalog file not found: {self._path}")

        raw_text = self._path.read_text(encoding="utf-8")
        data: dict[str, Any] = yaml.safe_load(raw_text) or {}
        catalog_file = CatalogFile(**data)
        catalog = catalog_file.catalog

        collected_at = datetime.now(timezone.utc)
        entity_count = 0
        membership_count = 0

        if clear:
            logger.info("Catalog sync: clearing all existing memberships")
            self._store.clear_catalog_memberships()  # type: ignore[attr-defined]

        matcher = CatalogMatcher(self._store)

        for entry in catalog.entities:
            type_display = catalog.type_display_name(entry.type)
            catalog_entity_id = f"catalog:{entry.type}:{entry.id}"

            # Build and upsert the catalog entity
            entity = self._build_entity(
                entry, catalog_entity_id, type_display, collected_at
            )
            self._store.upsert(entity)
            entity_count += 1

            # Evaluate matchers and upsert memberships
            memberships = matcher.evaluate(entry, catalog_entity_id, collected_at)
            for m in memberships:
                self._store.upsert(m)
                membership_count += 1

        logger.info(
            "Catalog sync complete: %d entities, %d memberships",
            entity_count,
            membership_count,
        )
        return {"entities": entity_count, "memberships": membership_count}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_entity(
        self,
        entry: CatalogEntityEntry,
        catalog_entity_id: str,
        type_display_name: str,
        collected_at: datetime,
    ) -> CatalogEntity:
        owning_team_id = None
        if entry.owning_team:
            team_slug = entry.owning_team
            owning_team_id = (
                team_slug if team_slug.startswith("team:") else f"team:{team_slug}"
            )

        criticality = entry.properties.get("criticality")

        return CatalogEntity(
            id=catalog_entity_id,
            provider_id=f"{entry.type}:{entry.id}",
            source_adapter=self.SOURCE_ADAPTER,
            collected_at=collected_at,
            type_id=entry.type,
            type_display_name=type_display_name,
            display_name=entry.display_name,
            description=entry.description,
            owning_team_id=owning_team_id,
            criticality=criticality,
            properties=entry.properties,
            raw=entry.model_dump(),
        )
