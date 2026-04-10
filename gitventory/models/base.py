"""Base entity that all inventory records inherit from."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InventoryEntity(BaseModel):
    """
    Root class for every object stored in the inventory.

    ID strategy
    -----------
    ``id`` is a stable, provider-namespaced key that never changes even when the
    upstream resource is renamed or transferred:

        github:12345678          — GitHub repository (numeric GitHub repo ID)
        azuredevops:<uuid>       — Azure DevOps repository
        aws:123456789012         — AWS account (12-digit account ID)
        azure:<subscription-id>  — Azure subscription (UUID)
        team:platform-eng        — Team (slug, owned by us — also stable)

    ``provider_id`` is the raw native identifier from the source system (the part
    after the colon).  It is kept for cross-referencing without string splitting.

    ``full_name`` / ``name`` fields on subclasses are *display* fields — mutable
    on each collect run and must never be used as foreign keys.
    """

    model_config = ConfigDict(
        # Allow arbitrary types in subclasses (e.g. datetime)
        arbitrary_types_allowed=True,
        # Pydantic v2: populate_by_name lets us use field names in addition to aliases
        populate_by_name=True,
    )

    id: str
    """Stable internal key: ``{provider}:{native_stable_id}``."""

    provider_id: str
    """Native stable ID from the source system (no provider prefix)."""

    source_adapter: str
    """Which adapter produced this entity, e.g. ``"github"``, ``"static_yaml"``."""

    collected_at: datetime
    """UTC timestamp of the collection run that produced this record."""

    raw: Optional[dict[str, Any]] = None
    """Original API/file payload — preserved for debugging and future field extraction."""

    @field_validator("collected_at", mode="before")
    @classmethod
    def ensure_utc(cls, v: Any) -> datetime:
        if isinstance(v, datetime):
            if v.tzinfo is None:
                return v.replace(tzinfo=timezone.utc)
            return v
        return v
