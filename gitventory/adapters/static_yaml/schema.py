"""Pydantic v2 schemas for the human-maintained inventory YAML files.

These are intentionally more permissive than the internal model entities —
they accept what humans write and the adapter maps them to InventoryEntity subclasses.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, model_validator


# ---------------------------------------------------------------------------
# teams.yaml
# ---------------------------------------------------------------------------

class ExternalIdentityEntry(BaseModel):
    """YAML representation of an external identity reference."""
    provider: str
    value: str
    metadata: dict[str, Any] = {}


class TeamEntry(BaseModel):
    id: str
    """Stable slug, e.g. ``"platform-engineering"``."""
    display_name: str
    # Legacy flat fields — kept for backwards compatibility; still parsed unchanged
    email: Optional[str] = None
    slack_channel: Optional[str] = None
    github_team_slug: Optional[str] = None
    members: list[str] = []
    # New structured fields — all optional so old-format files load without changes
    type: str = "team"
    identities: list[ExternalIdentityEntry] = []
    contacts: dict[str, str] = {}
    properties: dict[str, Any] = {}


class TeamsFile(BaseModel):
    teams: list[TeamEntry] = []


# ---------------------------------------------------------------------------
# aws_accounts.yaml
# ---------------------------------------------------------------------------

class AwsAccountEntry(BaseModel):
    id: str
    """12-digit AWS account ID (stored as string to preserve leading zeros, though
    AWS account IDs don't actually have them — kept consistent for safety)."""
    name: str
    environment: Optional[str] = None
    ou_path: Optional[str] = None
    owning_team: Optional[str] = None
    """Slug of the owning team — resolved to ``"team:{slug}"`` on ingest."""
    tags: dict[str, str] = {}


class AwsAccountsFile(BaseModel):
    accounts: list[AwsAccountEntry] = []


# ---------------------------------------------------------------------------
# deployment_mappings.yaml
# ---------------------------------------------------------------------------

class MappingEntry(BaseModel):
    repo: str
    """Full-name slug of the repository, e.g. ``"my-org/my-repo"``.
    The static_yaml adapter stores this as-is; the runner resolves it to a
    stable ``github:NNN`` ID when a GitHub collect has been run."""
    target_type: str = "cloud_account"
    target_id: str
    """Stable CloudAccount ID, e.g. ``"aws:123456789012"``."""
    deploy_method: Optional[str] = None
    environment: Optional[str] = None
    notes: Optional[str] = None


class DeploymentMappingsFile(BaseModel):
    mappings: list[MappingEntry] = []


# ---------------------------------------------------------------------------
# users.yaml  (enrichment only — not collected by the adapter itself)
# ---------------------------------------------------------------------------

class UserEntry(BaseModel):
    """One enrichment record in ``users.yaml``.

    Exactly one of ``user``, ``id``, or ``login`` must be provided:

    ``user``
        Login-based reference.  Accepts a bare login (``alice``) or a
        provider-scoped login (``github:user:alice``).  Always resolved via a
        ``User.login`` lookup.  Use the provider-scoped form when you have users
        from multiple providers with the same login name.

    ``id``
        Stable ID reference — the exact value stored in the database as
        ``User.id``, e.g. ``github:user:12345678``.  Resolved via an exact
        ``User.id`` match.  Use this when you need long-term stability even if
        the user renames their account.

    ``login``
        **Deprecated.** Treated identically to a bare ``user:`` value.  Kept so
        that existing ``users.yaml`` files continue to work without changes.
    """

    user: Optional[str] = None
    id: Optional[str] = None
    login: Optional[str] = None   # legacy alias for user:
    email: Optional[str] = None
    slack_handle: Optional[str] = None
    properties: dict[str, Any] = {}

    @model_validator(mode="after")
    def check_ref(self) -> "UserEntry":
        # Migrate legacy login: → user: silently
        if self.login and not self.user:
            self.user = self.login
        if not self.user and not self.id:
            raise ValueError("One of 'user', 'id', or 'login' must be set in each users.yaml entry")
        if self.user and self.id:
            raise ValueError("Only one of 'user' or 'id' may be set per entry, not both")
        return self


class UsersFile(BaseModel):
    users: list[UserEntry] = []
