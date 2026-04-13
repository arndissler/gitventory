"""RepoTeamAssignment — explicit many-to-many between repositories and teams."""

from __future__ import annotations

from gitventory.models.base import InventoryEntity


class RepoTeamAssignment(InventoryEntity):
    """
    Records that a GitHub team has been granted access to a repository,
    along with the permission level.

    ``id`` format: ``"rta:{repo_id}::{team_id}"``

    Example: ``"rta:github:12345678::github:team:9876"``

    ``repo_id`` → stable ``Repository.id``  (e.g. ``"github:12345678"``)
    ``team_id`` → stable ``Team.id``        (e.g. ``"github:team:9876"`` or
                                             ``"team:platform-eng"`` for YAML teams)
    ``org`` is stored to scope stale-row cleanup to a specific org without
    requiring a join back to the repository table.
    """

    repo_id: str
    """FK → Repository.id (stable provider-namespaced ID)."""

    team_id: str
    """FK → Team.id (``github:team:NNN`` for discovered teams, ``team:slug`` for YAML)."""

    permission: str
    """GitHub permission level: ``pull`` | ``triage`` | ``push`` | ``maintain`` | ``admin``."""

    org: str
    """GitHub organisation this assignment belongs to."""
