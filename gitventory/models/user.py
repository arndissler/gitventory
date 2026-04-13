"""User entity — a person discovered from a source control provider."""

from __future__ import annotations

from typing import Any, Optional

from gitventory.models.base import InventoryEntity


class User(InventoryEntity):
    """
    A user account discovered from a source control provider.

    ``id`` format: ``"{provider}:user:{numeric_id}"``

    Examples
    --------
    - ``github:user:1234567``   — GitHub user (numeric ID, survives login renames)
    - ``gitlab:user:7654321``   — GitLab user
    - ``azdo:user:{uuid}``      — Azure DevOps user

    The ``login`` field is the human-readable username.  It is MUTABLE — GitHub
    allows login renames — and must never be used as a foreign key.

    ``email`` and ``slack_handle`` are enrichment fields populated from
    ``users.yaml``; they are never set by the provider adapter itself.
    """

    provider: str
    """Source provider: ``"github"`` | ``"gitlab"`` | ``"azdo"``."""

    login: str
    """Username / handle on the provider.  Mutable — do not use as a FK."""

    display_name: Optional[str] = None
    """Full name from the provider profile, if available."""

    avatar_url: Optional[str] = None
    profile_url: Optional[str] = None

    # Enrichment fields — never set by the adapter, only by UserEnrichmentSyncer
    email: Optional[str] = None
    slack_handle: Optional[str] = None
    properties: dict[str, Any] = {}
