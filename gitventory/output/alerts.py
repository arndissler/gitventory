"""Alert-specific output helpers — priority scoring and grouped rendering."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Optional

from rich.table import Table

from gitventory.output.helpers import console

# ---------------------------------------------------------------------------
# Priority scoring
# ---------------------------------------------------------------------------

_SEVERITY_SCORES: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_CRITICALITY_WEIGHTS: dict[str, float] = {"critical": 2.0, "high": 1.5, "medium": 1.0, "low": 0.5}


def criticality_score(criticality: Optional[str]) -> float:
    """Return a numeric weight for a criticality label (for sorting/comparison)."""
    return _CRITICALITY_WEIGHTS.get(criticality or "", 1.0)


def weighted_priority(severity: Optional[str], criticality: Optional[str]) -> float:
    """Compute severity × criticality_weight.

    Original alert severity is never mutated — this is computed at display time only.
    A repo not linked to any catalog entity defaults to weight 1.0 (neutral).
    """
    s = _SEVERITY_SCORES.get(severity or "", 0)
    w = _CRITICALITY_WEIGHTS.get(criticality or "", 1.0)
    return s * w


# ---------------------------------------------------------------------------
# Priority-sorted flat output
# ---------------------------------------------------------------------------

def output_alerts_with_priority(
    alerts: list,
    criticality_by_repo: dict[str, Optional[str]],
    output_fmt: str,
) -> None:
    """Output alerts with an extra weighted_priority column."""
    cols = ["repo_id", "alert_type", "state", "severity", "weighted_priority",
            "secret_type", "rule_id", "created_at"]

    if output_fmt == "json":
        output_data = []
        for a in alerts:
            wp = weighted_priority(a.severity, criticality_by_repo.get(a.repo_id))
            output_data.append({
                "repo_id": a.repo_id,
                "alert_type": a.alert_type,
                "state": a.state,
                "severity": a.severity,
                "weighted_priority": wp,
                "secret_type": a.secret_type,
                "rule_id": a.rule_id,
                "created_at": str(a.created_at),
            })
        console.print_json(json.dumps(output_data))
        return

    table = Table(title=f"GHAS Alerts — sorted by weighted priority ({len(alerts)})")
    for col in cols:
        table.add_column(col.replace("_", " ").title())

    for a in alerts:
        wp = weighted_priority(a.severity, criticality_by_repo.get(a.repo_id))
        wp_str = f"{wp:.1f}"
        if wp >= 6:
            wp_str = f"[bold red]{wp_str}[/bold red]"
        elif wp >= 3:
            wp_str = f"[red]{wp_str}[/red]"
        elif wp >= 1.5:
            wp_str = f"[yellow]{wp_str}[/yellow]"

        table.add_row(
            a.repo_id or "",
            a.alert_type or "",
            a.state or "",
            a.severity or "[dim]—[/dim]",
            wp_str,
            a.secret_type or "[dim]—[/dim]",
            a.rule_id or "[dim]—[/dim]",
            str(a.created_at) if a.created_at else "[dim]—[/dim]",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# Grouped output
# ---------------------------------------------------------------------------

def _alert_dict(a) -> dict:
    """Serialise a GhasAlert to a plain dict for grouped JSON output."""
    return {
        "alert_id": a.id,
        "alert_type": a.alert_type,
        "state": a.state,
        "severity": a.severity,
        "rule_id": a.rule_id,
        "secret_type": a.secret_type,
        "created_at": str(a.created_at) if a.created_at else None,
        "url": a.url,
    }


def _repo_info(repo_id: Optional[str], repo_cache: dict, team_cache: dict) -> dict:
    """Return repo + owning-team contact fields for a given repo_id."""
    r = repo_cache.get(repo_id or "")
    t = team_cache.get(r.owning_team_id or "") if r else None
    return {
        "repo_id": repo_id,
        "repo_slug": r.full_name if r else None,
        "repo_url": r.url if r else None,
        "owning_team_id": r.owning_team_id if r else None,
        "team_email": (t.contacts.get("email") or t.email) if t else None,
        "team_slack_channel": (t.contacts.get("slack_channel") or t.slack_channel) if t else None,
    }


def _team_info(team_id: Optional[str], team_cache: dict) -> dict:
    """Return team contact fields for a given team_id."""
    t = team_cache.get(team_id or "")
    return {
        "team_id": team_id,
        "team_slug": t.slug if t else None,
        "team_email": (t.contacts.get("email") or t.email) if t else None,
        "team_slack_channel": (t.contacts.get("slack_channel") or t.slack_channel) if t else None,
    }


def output_alerts_grouped(
    alerts: list,
    groups: list[str],
    repo_cache: dict,
    team_cache: dict,
    output_fmt: str,
) -> None:
    """Output GHAS alerts grouped by repo and/or team.

    Supported group combinations:

    - ``["repo"]``              → one object per repo with ``alerts[]``
    - ``["team"]``              → one object per team with ``alerts[]``
    - ``["team", "repo"]``      → one object per team with ``repos[]{alerts[]}``

    When both ``team`` and ``repo`` are in *groups*, team is always the outer
    dimension regardless of order.  Both ``table`` and ``json`` output formats
    are supported.
    """
    use_team = "team" in groups
    use_repo = "repo" in groups

    if use_team and use_repo:
        _output_grouped_team_repo(alerts, repo_cache, team_cache, output_fmt)
    elif use_team:
        _output_grouped_team(alerts, repo_cache, team_cache, output_fmt)
    else:
        _output_grouped_repo(alerts, repo_cache, team_cache, output_fmt)


def _output_grouped_team_repo(
    alerts: list, repo_cache: dict, team_cache: dict, output_fmt: str
) -> None:
    """team → repos[] → alerts[]"""
    by_team: dict = defaultdict(lambda: defaultdict(list))
    for a in alerts:
        r = repo_cache.get(a.repo_id or "")
        tid = (r.owning_team_id if r else None) or "__unassigned__"
        by_team[tid][a.repo_id or "__unknown__"].append(a)

    if output_fmt == "json":
        output_data = []
        for tid, repos in by_team.items():
            obj = _team_info(tid if tid != "__unassigned__" else None, team_cache)
            obj["repos"] = []
            for rid, repo_alerts in repos.items():
                robj: dict = {
                    "repo_id": rid if rid != "__unknown__" else None,
                    "repo_slug": repo_cache[rid].full_name if rid in repo_cache else None,
                    "repo_url": repo_cache[rid].url if rid in repo_cache else None,
                    "alerts": [_alert_dict(a) for a in repo_alerts],
                }
                obj["repos"].append(robj)
            output_data.append(obj)
        console.print_json(json.dumps(output_data, default=str))
        return

    table = Table(title=f"GHAS Alerts grouped by team → repo ({len(alerts)})")
    for col in ["team_slug", "team_email", "team_slack_channel",
                "repo_slug", "alert_type", "state", "severity", "rule_id", "created_at"]:
        table.add_column(col.replace("_", " ").title())
    for tid, repos in by_team.items():
        ti = _team_info(tid if tid != "__unassigned__" else None, team_cache)
        for rid, repo_alerts in repos.items():
            ri = repo_cache.get(rid) if rid != "__unknown__" else None
            for a in repo_alerts:
                table.add_row(
                    ti["team_slug"] or "[dim]—[/dim]",
                    ti["team_email"] or "[dim]—[/dim]",
                    ti["team_slack_channel"] or "[dim]—[/dim]",
                    ri.full_name if ri else "[dim]—[/dim]",
                    a.alert_type or "", a.state or "",
                    a.severity or "[dim]—[/dim]",
                    a.rule_id or "[dim]—[/dim]",
                    str(a.created_at) if a.created_at else "[dim]—[/dim]",
                )
    console.print(table)


def _output_grouped_team(
    alerts: list, repo_cache: dict, team_cache: dict, output_fmt: str
) -> None:
    """team → alerts[]"""
    by_team: dict = defaultdict(list)
    for a in alerts:
        r = repo_cache.get(a.repo_id or "")
        tid = (r.owning_team_id if r else None) or "__unassigned__"
        by_team[tid].append(a)

    if output_fmt == "json":
        output_data = []
        for tid, team_alerts in by_team.items():
            obj = _team_info(tid if tid != "__unassigned__" else None, team_cache)
            obj["alerts"] = [_alert_dict(a) for a in team_alerts]
            output_data.append(obj)
        console.print_json(json.dumps(output_data, default=str))
        return

    table = Table(title=f"GHAS Alerts grouped by team ({len(alerts)})")
    for col in ["team_slug", "team_email", "team_slack_channel",
                "repo_id", "alert_type", "state", "severity", "rule_id", "created_at"]:
        table.add_column(col.replace("_", " ").title())
    for tid, team_alerts in by_team.items():
        ti = _team_info(tid if tid != "__unassigned__" else None, team_cache)
        for a in team_alerts:
            table.add_row(
                ti["team_slug"] or "[dim]—[/dim]",
                ti["team_email"] or "[dim]—[/dim]",
                ti["team_slack_channel"] or "[dim]—[/dim]",
                a.repo_id or "[dim]—[/dim]",
                a.alert_type or "", a.state or "",
                a.severity or "[dim]—[/dim]",
                a.rule_id or "[dim]—[/dim]",
                str(a.created_at) if a.created_at else "[dim]—[/dim]",
            )
    console.print(table)


def _output_grouped_repo(
    alerts: list, repo_cache: dict, team_cache: dict, output_fmt: str
) -> None:
    """repo → alerts[]"""
    by_repo: dict = defaultdict(list)
    for a in alerts:
        by_repo[a.repo_id or "__unknown__"].append(a)

    if output_fmt == "json":
        output_data = []
        for rid, repo_alerts in by_repo.items():
            obj = _repo_info(rid if rid != "__unknown__" else None, repo_cache, team_cache)
            obj["alerts"] = [_alert_dict(a) for a in repo_alerts]
            output_data.append(obj)
        console.print_json(json.dumps(output_data, default=str))
        return

    table = Table(title=f"GHAS Alerts grouped by repo ({len(alerts)})")
    for col in ["repo_slug", "repo_url", "owning_team_id",
                "team_email", "team_slack_channel",
                "alert_type", "state", "severity", "rule_id", "created_at"]:
        table.add_column(col.replace("_", " ").title())
    for rid, repo_alerts in by_repo.items():
        ri = _repo_info(rid if rid != "__unknown__" else None, repo_cache, team_cache)
        for a in repo_alerts:
            table.add_row(
                ri["repo_slug"] or "[dim]—[/dim]",
                ri["repo_url"] or "[dim]—[/dim]",
                ri["owning_team_id"] or "[dim]—[/dim]",
                ri["team_email"] or "[dim]—[/dim]",
                ri["team_slack_channel"] or "[dim]—[/dim]",
                a.alert_type or "", a.state or "",
                a.severity or "[dim]—[/dim]",
                a.rule_id or "[dim]—[/dim]",
                str(a.created_at) if a.created_at else "[dim]—[/dim]",
            )
    console.print(table)
