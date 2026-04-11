"""Catalog models — organizational meta-model entities."""

from __future__ import annotations

from typing import Any, Optional

from gitventory.models.base import InventoryEntity


class CatalogEntity(InventoryEntity):
    """An organizational entity defined in the catalog (service, project, domain, etc.).

    ID format: ``catalog:{type_id}:{entity_slug}``  e.g. ``catalog:service:checkout-api``

    Entity types are user-defined slugs — the catalog imposes no fixed hierarchy.
    Any number of entity types can coexist (service, project, application, domain, …).
    One repository or cloud account can belong to multiple catalog entities.
    """

    type_id: str
    """User-defined entity type slug, e.g. ``service``, ``project``."""

    type_display_name: str
    """Human-readable label for the type, e.g. ``"Service"``."""

    display_name: str
    """Mutable human-readable name — updated on every catalog sync."""

    description: Optional[str] = None

    owning_team_id: Optional[str] = None
    """Stable team ID, e.g. ``"team:platform-engineering"``."""

    criticality: Optional[str] = None
    """Promoted from ``properties["criticality"]`` for indexed querying.
    Typical values: ``critical``, ``high``, ``medium``, ``low``.
    Not enforced — user-defined values are stored as-is."""

    properties: dict[str, Any] = {}
    """Arbitrary key-value metadata defined per entity type in the catalog YAML."""


class CatalogMembership(InventoryEntity):
    """Link between a catalog entity and a technical artifact (repo or cloud account).

    ID format: ``membership:{catalog_entity_id}::{technical_entity_id}``

    Memberships are created at collect time by evaluating the declarative matchers
    defined in the catalog YAML.  They can be wiped and rebuilt without data loss
    (``gitventory catalog sync --clear``).
    """

    catalog_entity_id: str
    """Stable ID of the owning catalog entity, e.g. ``catalog:service:checkout-api``."""

    technical_entity_id: str
    """Stable ID of the linked technical artifact, e.g. ``github:12345678``."""

    technical_entity_type: str
    """Type of the linked entity: ``repository`` or ``cloud_account``."""

    matched_by: str
    """Human-readable description of the matcher rule that produced this link,
    e.g. ``"repos[0].full_name=my-org/checkout-api"`` or ``"repos[2].topics.any=[checkout]"``."""
