"""Repository entity — provider-agnostic (GitHub, Azure DevOps, …)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from gitventory.models.base import InventoryEntity


class Repository(InventoryEntity):
    """
    A source-code repository from any supported VCS provider.

    ``id`` format: ``"{provider}:{provider_id}"``
      - GitHub:        ``github:12345678``    (numeric GitHub repo ID — stable across renames)
      - Azure DevOps:  ``azuredevops:<uuid>`` (ADO repo UUID)

    ``full_name`` is a *mutable display field* updated on every collect run.
    Never use it as a foreign key.
    """

    provider: str
    """``"github"`` | ``"azuredevops"`` | …"""

    org: str
    """GitHub organisation name or Azure DevOps organisation name."""

    project: Optional[str] = None
    """Azure DevOps project name. None for GitHub."""

    name: str
    """Short repository name."""

    full_name: str
    """Human-readable slug, e.g. ``"my-org/my-repo"``. MUTABLE — updated each run."""

    url: str
    """Web URL of the repository."""

    language: Optional[str] = None
    topics: list[str] = []
    visibility: Literal["public", "private", "internal"] = "private"
    is_archived: bool = False
    is_fork: bool = False
    is_template: bool = False

    default_branch: str = "main"
    last_push_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    # Security posture — populated by GitHub adapter; nullable for other providers
    ghas_enabled: bool = False
    open_secret_alerts: int = 0
    open_code_scanning_alerts: int = 0
    open_dependabot_alerts: int = 0

    # Ownership — resolved by joining with Team entities
    owning_team_id: Optional[str] = None

    @property
    def has_open_alerts(self) -> bool:
        return (
            self.open_secret_alerts > 0
            or self.open_code_scanning_alerts > 0
            or self.open_dependabot_alerts > 0
        )
