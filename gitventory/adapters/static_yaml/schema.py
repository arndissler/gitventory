"""Pydantic v2 schemas for the human-maintained inventory YAML files.

These are intentionally more permissive than the internal model entities —
they accept what humans write and the adapter maps them to InventoryEntity subclasses.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# teams.yaml
# ---------------------------------------------------------------------------

class TeamEntry(BaseModel):
    id: str
    """Stable slug, e.g. ``"platform-engineering"``."""
    display_name: str
    email: Optional[str] = None
    slack_channel: Optional[str] = None
    github_team_slug: Optional[str] = None
    members: list[str] = []


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
