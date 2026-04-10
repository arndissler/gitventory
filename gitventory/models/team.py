"""Team entity — the ownership anchor for repositories and cloud accounts."""

from __future__ import annotations

from typing import Optional

from gitventory.models.base import InventoryEntity


class Team(InventoryEntity):
    """
    A team or group responsible for one or more repositories and/or cloud accounts.

    ``id`` format: ``"team:{slug}"``
    ``provider_id`` equals the slug (teams are defined by us, not an external system).

    The slug is the stable identifier — renaming the display_name does not affect
    ownership links stored in Repository.owning_team_id or CloudAccount.owning_team_id.
    """

    display_name: str
    """Human-readable team name. MUTABLE."""

    email: Optional[str] = None
    slack_channel: Optional[str] = None

    github_team_slug: Optional[str] = None
    """Slug of the corresponding GitHub team, for cross-referencing."""

    members: list[str] = []
    """GitHub usernames or email addresses of team members."""
