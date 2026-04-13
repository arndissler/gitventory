"""RepoCollaborator — direct or outside collaborator on a repository."""

from __future__ import annotations

from gitventory.models.base import InventoryEntity


class RepoCollaborator(InventoryEntity):
    """
    Records that a user has been granted direct access to a repository
    (i.e. not via team membership), along with their permission level
    and affiliation category.

    ``id`` format: ``"rc:{repo_id}::{user_id}::{affiliation}"``

    Example: ``"rc:github:12345678::github:user:111222::direct"``

    ``affiliation`` mirrors the GitHub API parameter:
    - ``"direct"``  — org member added directly to the repo
    - ``"outside"`` — collaborator who is not an org member
    """

    repo_id: str
    """FK → Repository.id."""

    user_id: str
    """FK → User.id (``github:user:NNN``)."""

    permission: str
    """GitHub permission: ``pull`` | ``triage`` | ``push`` | ``maintain`` | ``admin``."""

    affiliation: str
    """``direct`` or ``outside``."""
