"""Abstract store interface — the only surface the CLI, runner, and future Web UI touch."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Iterator, List, Optional, Type, TypeVar

from gitventory.models.base import InventoryEntity

E = TypeVar("E", bound=InventoryEntity)


class AbstractStore(ABC):
    """
    Backend-agnostic storage interface.

    All interaction with the data store goes through this interface so that
    swapping SQLite for PostgreSQL (or adding a future HTTP backend) requires
    only a new implementation here — no changes in adapters, runner, or CLI.
    """

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    @abstractmethod
    def upsert(self, entity: InventoryEntity) -> None:
        """Insert or update a single entity by its stable ``id``.  Idempotent."""
        ...

    @abstractmethod
    def upsert_many(self, entities: Iterator[InventoryEntity]) -> int:
        """Consume an iterator of entities, upsert each, and return the count written."""
        ...

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    @abstractmethod
    def get(self, entity_type: Type[E], entity_id: str) -> Optional[E]:
        """Fetch a single entity by its stable ``id``.  Returns None if not found."""
        ...

    @abstractmethod
    def query(self, entity_type: Type[E], filters: dict[str, Any]) -> List[E]:
        """
        Return entities matching all supplied filters.

        Filter dict conventions (mirrors SQL predicates):
          ``{"field": value}``           — equality
          ``{"field__gt": value}``       — greater-than
          ``{"field__lt": value}``       — less-than
          ``{"field__gte": value}``      — greater-than-or-equal
          ``{"field__lte": value}``      — less-than-or-equal
          ``{"field__contains": value}`` — substring / list-contains
          ``{"field__isnull": True}``    — field IS NULL

        Filters are ANDed together.
        """
        ...

    @abstractmethod
    def count(self, entity_type: Type[E]) -> int:
        """Return the total number of stored entities of the given type."""
        ...

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    @abstractmethod
    def last_collected_at(self, adapter_name: str) -> Optional[datetime]:
        """Return the timestamp of the last successful collection run for an adapter."""
        ...

    @abstractmethod
    def record_collection_run(
        self,
        adapter_name: str,
        started_at: datetime,
        finished_at: datetime,
        entity_count: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Persist a collection run record for auditing."""
        ...

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    def clear_catalog_memberships(self) -> None:
        """Delete all CatalogMembership records.  Used for re-hydration runs.

        Default implementation raises NotImplementedError.  Stores that support
        catalog sync must override this.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement clear_catalog_memberships()"
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def init_schema(self) -> None:
        """Create tables / indices if they do not yet exist.  Safe to call repeatedly."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Release any held resources (connections, file handles)."""
        ...

    def __enter__(self) -> "AbstractStore":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
