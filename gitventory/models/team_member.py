"""TeamMember — membership record linking a user to a team with a role."""

from __future__ import annotations

from gitventory.models.base import InventoryEntity


class TeamMember(InventoryEntity):
    """
    Records that a user is a member of a team, along with their role.

    ``id`` format: ``"tm:{team_id}::{user_id}"``

    Example: ``"tm:github:team:9876::github:user:111222"``

    ``role`` mirrors the GitHub team membership role:
    - ``"maintainer"`` — can manage team settings and members
    - ``"member"``     — regular team member
    """

    team_id: str
    """FK → Team.id (``github:team:NNN``)."""

    user_id: str
    """FK → User.id (``github:user:NNN``)."""

    role: str
    """``maintainer`` or ``member``."""

    org: str
    """GitHub organisation this membership belongs to."""
