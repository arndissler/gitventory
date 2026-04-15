"""GhasAlert entity — a GitHub Advanced Security finding on a repository."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from gitventory.models.base import InventoryEntity


class GhasAlert(InventoryEntity):
    """
    A single GHAS alert (secret scanning, code scanning, or Dependabot).

    ``id`` format: ``"{repo_id}::alert::{alert_type}::{number}"``
    e.g.  ``"github:12345678::alert::secret_scanning::42"``

    ``repo_id`` is the stable Repository.id — survives repo renames.
    """

    repo_id: str
    """FK → Repository.id (stable ``github:NNN``)."""

    alert_type: Literal["secret_scanning", "code_scanning", "dependabot"]

    number: int
    """Alert number within the repository (as returned by the GitHub API)."""

    state: Literal["open", "dismissed", "fixed", "auto_dismissed", "resolved"] = "open"

    severity: Optional[str] = None
    """``"critical"`` | ``"high"`` | ``"medium"`` | ``"low"`` | ``"warning"`` | ``"note"``"""

    rule_id: Optional[str] = None
    """Code scanning rule ID or Dependabot advisory ID."""

    secret_type: Optional[str] = None
    """Secret scanning only: e.g. ``"github_personal_access_token"``."""

    secret_type_display_name: Optional[str] = None
    """Human-readable secret type name."""

    created_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None
    dismissed_reason: Optional[str] = None
    fixed_at: Optional[datetime] = None

    url: str = ""
    """URL to the alert on GitHub."""
