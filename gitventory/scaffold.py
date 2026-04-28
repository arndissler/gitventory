"""Scaffold helpers — generate stub YAML entries from live DB state.

``scaffold_teams`` and ``scaffold_accounts`` each support three modes:

- ``diff=True``     Print a diff of DB vs file to stdout. No file changes.
- ``dry_run=True``  Print what *would* be appended. No file changes.
- default           Append missing stubs to the file (never touch existing entries).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from gitventory.output.helpers import console

# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------


def _gh_team_key(team) -> str:
    """Canonical human-readable key for a GitHub-discovered team: ``org/slug``."""
    return f"{team.github_org}/{team.github_team_slug}"


def _entry_matches_github_team(entry, team) -> bool:
    """Return True if a TeamEntry from file corresponds to a GitHub Team in the DB.

    Three tiers, in priority order:

    1. Explicit ``github_team`` identity with ``org/slug`` value (canonical).
    2. Explicit ``github_team`` identity with numeric ``github:team:{id}`` value.
    3. Entry ``id`` equals ``org/slug``.
    4. Legacy ``github_team_slug`` field matches the team's slug (least precise —
       ignored when the org cannot be confirmed).
    """
    key = _gh_team_key(team)

    for ident in entry.identities:
        if ident.provider == "github_team":
            if ident.value == key:
                return True
            if ident.value == team.id:  # e.g. "github:team:12345678"
                return True

    if entry.id == key:
        return True

    if entry.github_team_slug and entry.github_team_slug == team.github_team_slug:
        return True

    return False


def _entry_matches_account(entry, account) -> bool:
    """Return True if an AwsAccountEntry from file corresponds to a CloudAccount in the DB."""
    return entry.id == account.provider_id


# ---------------------------------------------------------------------------
# Stub generators
# ---------------------------------------------------------------------------


def _team_stub_dict(team) -> dict:
    """Minimal teams.yaml entry dict for a GitHub team — human fills in the rest."""
    key = _gh_team_key(team)
    return {
        "id": key,
        "display_name": team.display_name,
        "identities": [{"provider": "github_team", "value": key}],
    }


def _account_stub_dict(account) -> dict:
    """Minimal aws_accounts.yaml entry dict for a CloudAccount."""
    entry: dict = {"id": account.provider_id, "name": account.name or account.provider_id}
    if account.environment:
        entry["environment"] = account.environment
    return entry


# ---------------------------------------------------------------------------
# YAML serialisation
# ---------------------------------------------------------------------------


def _yaml_scalar(value: str) -> str:
    """Return a YAML-safe scalar representation (adds quotes when needed)."""
    return yaml.dump(value, default_flow_style=True, allow_unicode=True).strip()


def _render_team_entry(d: dict) -> str:
    """Render a team stub dict as properly indented YAML text (under ``teams:``)."""
    lines = [
        f"  - id: {_yaml_scalar(d['id'])}",
        f"    display_name: {_yaml_scalar(d['display_name'])}",
    ]
    if d.get("identities"):
        lines.append("    identities:")
        for ident in d["identities"]:
            lines.append(f"      - provider: {_yaml_scalar(ident['provider'])}")
            lines.append(f"        value: {_yaml_scalar(ident['value'])}")
    return "\n".join(lines)


def _render_account_entry(d: dict) -> str:
    """Render an account stub dict as properly indented YAML text (under ``accounts:``)."""
    # Always quote account IDs so editors and diff tools don't auto-convert them
    lines = [
        f'  - id: "{d["id"]}"',
        f"    name: {_yaml_scalar(d['name'])}",
    ]
    if d.get("environment"):
        lines.append(f"    environment: {_yaml_scalar(d['environment'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def _load_teams_file(path: Path):
    from gitventory.adapters.static_yaml.schema import TeamsFile

    if not path.exists():
        return TeamsFile()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return TeamsFile.model_validate(raw)


def _load_accounts_file(path: Path):
    from gitventory.adapters.static_yaml.schema import AwsAccountsFile

    if not path.exists():
        return AwsAccountsFile()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AwsAccountsFile.model_validate(raw)


def _ensure_file_with_key(path: Path, top_key: str) -> None:
    """Create the YAML file with an empty list if it doesn't exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{top_key}:\n", encoding="utf-8")


def _append_entries(path: Path, rendered_entries: list[str]) -> None:
    """Append rendered YAML entries to an existing file, preserving its content."""
    existing = path.read_text(encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        if not existing.endswith("\n"):
            fh.write("\n")
        for block in rendered_entries:
            fh.write(block)
            fh.write("\n")


# ---------------------------------------------------------------------------
# Public scaffold functions
# ---------------------------------------------------------------------------


def scaffold_teams(
    store,
    path: Path,
    dry_run: bool = False,
    diff: bool = False,
) -> None:
    """Scaffold ``teams.yaml`` from GitHub teams in the store.

    Only adds entries — never touches existing ones.
    """
    from gitventory.models.team import Team

    file_data = _load_teams_file(path)
    all_teams = store.query(Team, {})
    github_teams = [t for t in all_teams if t.id.startswith("github:team:")]

    # Partition into matched / unmatched on both sides
    matched_gh_ids: set[str] = set()
    matched_entry_ids: set[str] = set()

    for gh_team in github_teams:
        for entry in file_data.teams:
            if _entry_matches_github_team(entry, gh_team):
                matched_gh_ids.add(gh_team.id)
                matched_entry_ids.add(entry.id)
                break

    unmatched_teams = [t for t in github_teams if t.id not in matched_gh_ids]
    unmatched_entries = [e for e in file_data.teams if e.id not in matched_entry_ids]

    if diff:
        _diff_teams(unmatched_teams, unmatched_entries, path)
        return

    if not unmatched_teams:
        console.print(
            f"[green]✓[/green] teams: all {len(github_teams)} GitHub team(s) already represented in [bold]{path}[/bold]"
        )
        return

    stubs = [_team_stub_dict(t) for t in unmatched_teams]
    rendered = [_render_team_entry(d) for d in stubs]

    if dry_run:
        console.print(
            f"[bold]Would append {len(stubs)} team(s) to {path}[/bold] [dim](dry run)[/dim]"
        )
        for block in rendered:
            console.print(block)
        return

    _ensure_file_with_key(path, "teams")
    _append_entries(path, rendered)
    console.print(f"[green]✓[/green] Appended {len(stubs)} team(s) to [bold]{path}[/bold]")
    for d in stubs:
        console.print(f"    + {d['id']}")


def scaffold_accounts(
    store,
    path: Path,
    dry_run: bool = False,
    diff: bool = False,
) -> None:
    """Scaffold ``aws_accounts.yaml`` from CloudAccounts in the store.

    Only adds entries — never touches existing ones.
    """
    from gitventory.models.cloud_account import CloudAccount

    file_data = _load_accounts_file(path)
    all_accounts = store.query(CloudAccount, {})

    matched_db_ids: set[str] = set()
    matched_entry_ids: set[str] = set()

    for account in all_accounts:
        for entry in file_data.accounts:
            if _entry_matches_account(entry, account):
                matched_db_ids.add(account.id)
                matched_entry_ids.add(entry.id)
                break

    unmatched_accounts = [a for a in all_accounts if a.id not in matched_db_ids]
    unmatched_entries = [e for e in file_data.accounts if e.id not in matched_entry_ids]

    if diff:
        _diff_accounts(unmatched_accounts, unmatched_entries, path)
        return

    if not unmatched_accounts:
        console.print(
            f"[green]✓[/green] accounts: all {len(all_accounts)} account(s) already represented in [bold]{path}[/bold]"
        )
        return

    stubs = [_account_stub_dict(a) for a in unmatched_accounts]
    rendered = [_render_account_entry(d) for d in stubs]

    if dry_run:
        console.print(
            f"[bold]Would append {len(stubs)} account(s) to {path}[/bold] [dim](dry run)[/dim]"
        )
        for block in rendered:
            console.print(block)
        return

    _ensure_file_with_key(path, "accounts")
    _append_entries(path, rendered)
    console.print(f"[green]✓[/green] Appended {len(stubs)} account(s) to [bold]{path}[/bold]")
    for d in stubs:
        console.print(f"    + {d['id']}")


# ---------------------------------------------------------------------------
# Diff output (always to stdout)
# ---------------------------------------------------------------------------


def _diff_teams(unmatched_teams, unmatched_entries, path: Path) -> None:
    console.print(f"[bold]Team diff — {path}[/bold]")

    if unmatched_teams:
        console.print(
            f"\n[yellow]In database, not in file[/yellow] "
            f"[dim]({len(unmatched_teams)} entry/entries)[/dim]"
        )
        for t in unmatched_teams:
            console.print(f"  [green]+[/green] {_gh_team_key(t)}  [dim]({t.display_name})[/dim]")
    else:
        console.print("\n[dim]In database, not in file: none[/dim]")

    if unmatched_entries:
        console.print(
            f"\n[red]In file, not in database[/red] "
            f"[dim]({len(unmatched_entries)} entry/entries)[/dim]"
        )
        for e in unmatched_entries:
            console.print(f"  [red]-[/red] {e.id}  [dim]({e.display_name})[/dim]")
    else:
        console.print("\n[dim]In file, not in database: none[/dim]")


def _diff_accounts(unmatched_accounts, unmatched_entries, path: Path) -> None:
    console.print(f"[bold]Account diff — {path}[/bold]")

    if unmatched_accounts:
        console.print(
            f"\n[yellow]In database, not in file[/yellow] "
            f"[dim]({len(unmatched_accounts)} entry/entries)[/dim]"
        )
        for a in unmatched_accounts:
            label = a.name or a.provider_id
            console.print(f"  [green]+[/green] {a.provider_id}  [dim]({label})[/dim]")
    else:
        console.print("\n[dim]In database, not in file: none[/dim]")

    if unmatched_entries:
        console.print(
            f"\n[red]In file, not in database[/red] "
            f"[dim]({len(unmatched_entries)} entry/entries)[/dim]"
        )
        for e in unmatched_entries:
            console.print(f"  [red]-[/red] {e.id}  [dim]({e.name})[/dim]")
    else:
        console.print("\n[dim]In file, not in database: none[/dim]")
