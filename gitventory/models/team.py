"""Team entity — the ownership anchor for repositories and cloud accounts."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from gitventory.models.base import InventoryEntity


class ExternalIdentity(BaseModel):
    """A reference to this org party in an external identity system.

    ``provider`` is a free-form string.  Recognised values (not enforced):
    ``github_team``, ``github_user``, ``entraid_group``, ``ldap_group``,
    ``okta_group``, ``slack_usergroup``.

    Examples
    --------
    - ``{"provider": "github_team", "value": "my-org/platform-engineering"}``
    - ``{"provider": "entraid_group", "value": "aaaabbbb-cccc-dddd-eeee-ffffffffffff"}``
    """

    provider: str
    value: str
    metadata: dict[str, Any] = {}


class Team(InventoryEntity):
    """
    An org party (team, squad, chapter, guild, …) responsible for one or more
    repositories and/or cloud accounts.

    ``id`` format: ``"team:{slug}"``
    ``provider_id`` equals the slug (teams are defined by us, not an external system).

    The slug is the stable identifier — renaming the display_name does not affect
    ownership links stored in Repository.owning_team_id or CloudAccount.owning_team_id.
    """

    display_name: str
    """Human-readable team name. MUTABLE."""

    # Legacy flat fields — kept for backwards compatibility
    email: Optional[str] = None
    slack_channel: Optional[str] = None
    github_team_slug: Optional[str] = None
    """Legacy single-org GitHub team slug.  Prefer identities[].provider=github_team."""

    members: list[str] = []
    """GitHub usernames or email addresses of team members."""

    # New structured fields
    type_id: str = "team"
    """User-defined type slug: team, squad, chapter, guild, virtual, …"""

    identities: list[ExternalIdentity] = []
    """Structured external identity mappings (multi-provider)."""

    contacts: dict[str, str] = {}
    """Contact channels, e.g. {slack_channel, jira_project, pagerduty_schedule, email}."""

    properties: dict[str, Any] = {}
    """Arbitrary metadata, e.g. {cost_center, location, on_call_rotation}."""

    # GitHub-discovered team fields (None for YAML-defined teams)
    parent_team_id: Optional[str] = None
    """Stable ID of the parent team for nested GitHub teams (``github:team:NNN``)."""

    github_org: Optional[str] = None
    """GitHub organisation this team belongs to.  Set for discovered teams only."""
