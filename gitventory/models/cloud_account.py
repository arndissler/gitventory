"""CloudAccount entity — provider-agnostic (AWS, Azure, …)."""

from __future__ import annotations

from typing import Optional

from gitventory.models.base import InventoryEntity


class CloudAccount(InventoryEntity):
    """
    A cloud account or subscription from any supported cloud provider.

    ``id`` format: ``"{provider}:{provider_id}"``
      - AWS:   ``aws:123456789012``          (12-digit account ID — inherently stable)
      - Azure: ``azure:<subscription-uuid>`` (subscription UUID)

    ``name`` is a mutable display field updated on every collect run.
    """

    provider: str
    """``"aws"`` | ``"azure"`` | …"""

    name: str
    """Human-readable account/subscription name. MUTABLE."""

    environment: Optional[str] = None
    """``"prod"`` | ``"staging"`` | ``"dev"`` | ``"sandbox"`` | …"""

    ou_path: Optional[str] = None
    """AWS Organizational Unit path (e.g. ``/root/workloads/prod``) or
    Azure Management Group path."""

    owning_team_id: Optional[str] = None
    """FK → Team.id  (``"team:<slug>"``)."""

    tags: dict[str, str] = {}
    """Cloud-provider tags/labels on the account."""
