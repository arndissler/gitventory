"""QueryBuilder — translate CLI-style filter arguments into store filter dicts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional


def build_repo_filters(
    repo: Optional[str] = None,
    org: Optional[str] = None,
    provider: Optional[str] = None,
    team: Optional[str] = None,
    stale_days: Optional[int] = None,
    has_alerts: bool = False,
    is_archived: Optional[bool] = None,
    extra: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Build a filter dict for Repository queries from CLI options."""
    filters: dict[str, Any] = {}

    if repo:
        # Accept stable ID (github:NNN) or full_name (org/repo)
        if repo.startswith("github:"):
            filters["id"] = repo
        else:
            filters["full_name"] = repo

    if org:
        filters["org"] = org
    if provider:
        filters["provider"] = provider
    if team:
        filters["owning_team_id"] = f"team:{team}" if not team.startswith("team:") else team
    if stale_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
        filters["last_push_at__lt"] = cutoff
    if has_alerts:
        # At least one alert type must be > 0; we use a synthetic filter key
        filters["has_open_alerts"] = True
    if is_archived is not None:
        filters["is_archived"] = is_archived

    # Parse generic -f "key=value" / "key>value" / "key<value" expressions
    for expr in extra or []:
        _parse_expr(expr, filters)

    return filters


def build_account_filters(
    provider: Optional[str] = None,
    env: Optional[str] = None,
    team: Optional[str] = None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if provider:
        filters["provider"] = provider
    if env:
        filters["environment"] = env
    if team:
        filters["owning_team_id"] = f"team:{team}" if not team.startswith("team:") else team
    return filters


def build_alert_filters(
    alert_type: Optional[str] = None,
    severity: Optional[str] = None,
    repo_id: Optional[str] = None,
    state: str = "open",
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if alert_type:
        filters["alert_type"] = alert_type
    if severity:
        filters["severity"] = severity
    if repo_id:
        filters["repo_id"] = repo_id
    if state != "all":
        filters["state"] = state
    return filters


def build_mapping_filters(
    repo_id: Optional[str] = None,
    account_id: Optional[str] = None,
    detection_method: Optional[str] = None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if repo_id:
        filters["repo_id"] = repo_id
    if account_id:
        filters["target_id"] = account_id
    if detection_method:
        filters["detection_method"] = detection_method
    return filters


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_expr(expr: str, filters: dict[str, Any]) -> None:
    """Parse a simple filter expression like ``"is_archived=false"`` or ``"open_secret_alerts>0"``."""
    for op, suffix in [(">=", "__gte"), ("<=", "__lte"), (">", "__gt"), ("<", "__lt"), ("=", "")]:
        if op in expr:
            key, _, raw_val = expr.partition(op)
            key = key.strip()
            raw_val = raw_val.strip()
            value: Any = _coerce(raw_val)
            filters[f"{key}{suffix}"] = value
            return
    # No operator found — ignore silently (could warn)


def _coerce(raw: str) -> Any:
    """Convert a raw string value to int, bool, or leave as str."""
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw
