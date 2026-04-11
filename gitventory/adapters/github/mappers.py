"""Mappers: raw GitHub API objects → InventoryEntity instances."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from github.Repository import Repository as GHRepository

from gitventory.models.ghas_alert import GhasAlert
from gitventory.models.repository import Repository


def repo_to_entity(
    gh_repo: GHRepository,
    collected_at: datetime,
    open_secret_alerts: int = 0,
    open_code_scanning_alerts: int = 0,
    open_dependabot_alerts: int = 0,
) -> Repository:
    """Convert a PyGithub Repository object to our Repository entity."""

    # Determine GHAS status from security_and_analysis if available
    ghas_enabled = False
    saa = getattr(gh_repo, "security_and_analysis", None)
    if saa:
        adv_sec = getattr(saa, "advanced_security", None)
        if adv_sec and getattr(adv_sec, "status", None) == "enabled":
            ghas_enabled = True

    visibility: str = gh_repo.visibility or "private"
    if visibility not in ("public", "private", "internal"):
        visibility = "private"

    return Repository(
        id=f"github:{gh_repo.id}",
        provider_id=str(gh_repo.id),
        provider="github",
        source_adapter="github",
        collected_at=collected_at,
        org=gh_repo.owner.login,
        name=gh_repo.name,
        full_name=gh_repo.full_name,
        url=gh_repo.html_url,
        language=gh_repo.language,
        topics=list(gh_repo.get_topics()),
        visibility=visibility,  # type: ignore[arg-type]
        is_archived=gh_repo.archived,
        is_fork=gh_repo.fork,
        is_template=getattr(gh_repo, "is_template", False) or False,
        default_branch=gh_repo.default_branch or "main",
        last_push_at=_utc(gh_repo.pushed_at),
        created_at=_utc(gh_repo.created_at),
        ghas_enabled=ghas_enabled,
        open_secret_alerts=open_secret_alerts,
        open_code_scanning_alerts=open_code_scanning_alerts,
        open_dependabot_alerts=open_dependabot_alerts,
        raw={
            "id": gh_repo.id,
            "full_name": gh_repo.full_name,
            "visibility": gh_repo.visibility,
            "archived": gh_repo.archived,
            # GitHub custom properties — used by catalog github_property matchers.
            # Returns a dict if the API exposes them; empty dict otherwise.
            "custom_properties": getattr(gh_repo, "custom_properties", None) or {},
        },
    )


def secret_alert_to_entity(
    alert: Any,
    repo_id: str,
    collected_at: datetime,
) -> GhasAlert:
    """Convert a PyGithub secret scanning alert to our GhasAlert entity."""
    number = alert.number
    return GhasAlert(
        id=f"{repo_id}::alert::secret_scanning::{number}",
        provider_id=str(number),
        source_adapter="github",
        collected_at=collected_at,
        repo_id=repo_id,
        alert_type="secret_scanning",
        number=number,
        state=alert.state,
        secret_type=getattr(alert, "secret_type", None),
        secret_type_display_name=getattr(alert, "secret_type_display_name", None),
        created_at=_utc(getattr(alert, "created_at", None)),
        dismissed_at=_utc(getattr(alert, "resolved_at", None)),
        dismissed_reason=getattr(alert, "resolution", None),
        url=getattr(alert, "html_url", ""),
        raw={"number": number, "state": alert.state},
    )


def code_scanning_alert_to_entity(
    alert: Any,
    repo_id: str,
    collected_at: datetime,
) -> GhasAlert:
    """Convert a PyGithub code scanning alert to our GhasAlert entity."""
    number = alert.number
    rule = getattr(alert, "rule", None)
    rule_id = getattr(rule, "id", None) if rule else None
    severity = None
    if rule:
        severity = getattr(rule, "security_severity_level", None) or getattr(rule, "severity", None)

    return GhasAlert(
        id=f"{repo_id}::alert::code_scanning::{number}",
        provider_id=str(number),
        source_adapter="github",
        collected_at=collected_at,
        repo_id=repo_id,
        alert_type="code_scanning",
        number=number,
        state=getattr(alert, "state", "open"),
        severity=severity,
        rule_id=rule_id,
        created_at=_utc(getattr(alert, "created_at", None)),
        dismissed_at=_utc(getattr(alert, "dismissed_at", None)),
        dismissed_reason=getattr(alert, "dismissed_reason", None),
        url=getattr(alert, "html_url", ""),
        raw={"number": number, "rule_id": rule_id},
    )


def dependabot_alert_to_entity(
    alert: Any,
    repo_id: str,
    collected_at: datetime,
) -> GhasAlert:
    """Convert a PyGithub Dependabot alert to our GhasAlert entity."""
    number = alert.number
    advisory = getattr(alert, "security_advisory", None)
    severity = getattr(advisory, "severity", None) if advisory else None
    rule_id = getattr(advisory, "ghsa_id", None) if advisory else None

    return GhasAlert(
        id=f"{repo_id}::alert::dependabot::{number}",
        provider_id=str(number),
        source_adapter="github",
        collected_at=collected_at,
        repo_id=repo_id,
        alert_type="dependabot",
        number=number,
        state=getattr(alert, "state", "open"),
        severity=severity,
        rule_id=rule_id,
        created_at=_utc(getattr(alert, "created_at", None)),
        dismissed_at=_utc(getattr(alert, "dismissed_at", None)),
        dismissed_reason=getattr(alert, "dismissed_reason", None),
        url=getattr(alert, "html_url", ""),
        raw={"number": number, "ghsa_id": rule_id, "severity": severity},
    )


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
