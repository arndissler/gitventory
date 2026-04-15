"""gitventory CLI — entry point for all commands."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from gitventory.config import load_config
from gitventory.store import create_store

console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(package_name="gitventory")
@click.option(
    "-c", "--config",
    default="config.yaml",
    show_default=True,
    help="Path to configuration file.",
    envvar="GITVENTORY_CONFIG",
)
@click.pass_context
def main(ctx: click.Context, config: str) -> None:
    """gitventory — modular inventory for repositories and cloud accounts."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------

@main.command()
@click.option("-a", "--adapter", "adapters", multiple=True, help="Run only this adapter (repeatable).")
@click.option("--repo", default=None, help="Collect only this repository (org/name or github:NNN).")
@click.option("--dry-run", is_flag=True, help="Collect but do not write to store.")
@click.option("--no-validate", is_flag=True, help="Skip connectivity pre-check.")
@click.option("-v", "--verbose", is_flag=True, help="Enable DEBUG logging.")
@click.pass_context
def collect(
    ctx: click.Context,
    adapters: tuple[str, ...],
    repo: Optional[str],
    dry_run: bool,
    no_validate: bool,
    verbose: bool,
) -> None:
    """Run adapters and populate the store."""
    _setup_logging(verbose)

    config = _load_config(ctx)
    with create_store(config.store) as store:
        from gitventory.runner import CollectionRunner
        runner = CollectionRunner(config, store)
        results = runner.run(
            adapter_names=list(adapters) or None,
            repo=repo,
            dry_run=dry_run,
            validate=not no_validate,
        )

    if dry_run:
        console.print("[bold yellow]Dry run — nothing written to store.[/bold yellow]")

    total = sum(results.values())
    for name, count in results.items():
        status = "[green]OK[/green]" if count > 0 else "[yellow]--[/yellow]"
        console.print(f"  {status} [bold]{name}[/bold]: {count} entities")
    console.print(f"\n[bold]Total: {total} entities[/bold]")


# ---------------------------------------------------------------------------
# query group
# ---------------------------------------------------------------------------

@main.group()
def query() -> None:
    """Query the inventory store."""


@query.command("repos")
@click.option("--repo", default=None, help="Filter by repository full_name (org/name) or stable ID (github:NNN).")
@click.option("--catalog-entity", "catalog_entity", default=None, help="Filter by catalog entity slug or stable ID.")
@click.option("--provider", default=None, help="Filter by provider (github, azuredevops).")
@click.option("--org", default=None, help="Filter by organisation.")
@click.option("--team", default=None, help="Filter by owning team slug.")
@click.option("--stale-days", type=int, default=None, help="Repos not pushed to in N days.")
@click.option("--has-alerts", is_flag=True, help="Only repos with open GHAS alerts.")
@click.option("--archived/--no-archived", default=None, help="Filter by archived status.")
@click.option("-f", "--filter", "extra_filters", multiple=True, help="Generic filter expression (e.g. 'open_secret_alerts>0').")
@click.option("-o", "--output", "output_fmt", default="table", type=click.Choice(["table", "json"]), show_default=True)
@click.option("--fields", default=None, help="Comma-separated list of columns to display.")
@click.pass_context
def query_repos(
    ctx: click.Context,
    repo: Optional[str],
    catalog_entity: Optional[str],
    provider: Optional[str],
    org: Optional[str],
    team: Optional[str],
    stale_days: Optional[int],
    has_alerts: bool,
    archived: Optional[bool],
    extra_filters: tuple[str, ...],
    output_fmt: str,
    fields: Optional[str],
) -> None:
    """List repositories matching the given filters."""
    from gitventory.models import CatalogMembership, Repository
    from gitventory.store.query import build_repo_filters

    config = _load_config(ctx)
    filters = build_repo_filters(
        repo=repo,
        org=org,
        provider=provider,
        team=team,
        stale_days=stale_days,
        has_alerts=has_alerts,
        is_archived=archived,
        extra=list(extra_filters),
    )

    with create_store(config.store) as store:
        if catalog_entity:
            entity = _resolve_catalog_entity(store, catalog_entity)
            if entity is None:
                err_console.print(f"[red]Catalog entity not found:[/red] {catalog_entity}")
                sys.exit(1)
            memberships = store.query(
                CatalogMembership,
                {"catalog_entity_id": entity.id, "technical_entity_type": "repository"},
            )
            repo_ids = {m.technical_entity_id for m in memberships}
            all_repos = store.query(Repository, filters)
            results = [r for r in all_repos if r.id in repo_ids]
        else:
            results = store.query(Repository, filters)

    if not results:
        console.print("[dim]No repositories found.[/dim]")
        return

    default_fields = ["id", "full_name", "provider", "language", "visibility",
                      "is_archived", "last_push_at", "open_secret_alerts",
                      "open_code_scanning_alerts", "open_dependabot_alerts", "owning_team_id"]
    cols = fields.split(",") if fields else default_fields

    _output(results, cols, output_fmt, "Repositories")


@query.command("catalog")
@click.option("--type", "type_id", default=None, help="Filter by entity type (e.g. service, project).")
@click.option("--criticality", default=None, help="Filter by criticality (critical, high, medium, low).")
@click.option("--team", default=None, help="Filter by owning team slug.")
@click.option("-o", "--output", "output_fmt", default="table", type=click.Choice(["table", "json"]), show_default=True)
@click.pass_context
def query_catalog(
    ctx: click.Context,
    type_id: Optional[str],
    criticality: Optional[str],
    team: Optional[str],
    output_fmt: str,
) -> None:
    """List catalog entities (services, projects, etc.) matching the given filters."""
    from gitventory.models import CatalogEntity
    from gitventory.store.query import build_catalog_filters

    config = _load_config(ctx)
    filters = build_catalog_filters(type_id=type_id, criticality=criticality, team=team)

    with create_store(config.store) as store:
        results = store.query(CatalogEntity, filters)

    if not results:
        console.print("[dim]No catalog entities found.[/dim]")
        return

    cols = ["id", "type_id", "display_name", "criticality", "owning_team_id", "description"]
    _output(results, cols, output_fmt, "Catalog Entities")


@query.command("teams")
@click.option("--type", "type_id", default=None, help="Filter by party type (team, squad, chapter, guild, …).")
@click.option("-o", "--output", "output_fmt", default="table", type=click.Choice(["table", "json"]), show_default=True)
@click.pass_context
def query_teams(
    ctx: click.Context,
    type_id: Optional[str],
    output_fmt: str,
) -> None:
    """List org parties (teams, squads, chapters, …)."""
    from gitventory.models import Team

    config = _load_config(ctx)
    filters: dict = {}
    if type_id:
        filters["type_id"] = type_id

    with create_store(config.store) as store:
        results = store.query(Team, filters)

    if not results:
        console.print("[dim]No teams found.[/dim]")
        return

    cols = ["id", "type_id", "display_name", "email", "slack_channel", "github_team_slug"]
    _output(results, cols, output_fmt, "Teams")


@query.command("accounts")
@click.option("--provider", default=None, help="Filter by provider (aws, azure).")
@click.option("--env", default=None, help="Filter by environment (prod, staging, dev).")
@click.option("--team", default=None, help="Filter by owning team slug.")
@click.option("-o", "--output", "output_fmt", default="table", type=click.Choice(["table", "json"]), show_default=True)
@click.pass_context
def query_accounts(
    ctx: click.Context,
    provider: Optional[str],
    env: Optional[str],
    team: Optional[str],
    output_fmt: str,
) -> None:
    """List cloud accounts matching the given filters."""
    from gitventory.models import CloudAccount
    from gitventory.store.query import build_account_filters

    config = _load_config(ctx)
    filters = build_account_filters(provider=provider, env=env, team=team)

    with create_store(config.store) as store:
        results = store.query(CloudAccount, filters)

    if not results:
        console.print("[dim]No accounts found.[/dim]")
        return

    cols = ["id", "provider", "name", "environment", "ou_path", "owning_team_id"]
    _output(results, cols, output_fmt, "Cloud Accounts")


@query.command("alerts")
@click.option("--type", "alert_type", default=None, type=click.Choice(["secret_scanning", "code_scanning", "dependabot"]), help="Alert type.")
@click.option("--severity", default=None, help="Filter by severity (critical, high, medium, low).")
@click.option("--repo", "repo_id", default=None, help="Filter by repository ID or full_name slug.")
@click.option("--catalog-entity", "catalog_entity", default=None, help="Filter by catalog entity slug or stable ID.")
@click.option("--state", default="open", type=click.Choice(["open", "dismissed", "fixed", "resolved", "all"]), show_default=True)
@click.option("--sort-by", "sort_by", default=None, type=click.Choice(["weighted-priority"]), help="Sort results.")
@click.option("-o", "--output", "output_fmt", default="table", type=click.Choice(["table", "json"]), show_default=True)
@click.pass_context
def query_alerts(
    ctx: click.Context,
    alert_type: Optional[str],
    severity: Optional[str],
    repo_id: Optional[str],
    catalog_entity: Optional[str],
    state: str,
    sort_by: Optional[str],
    output_fmt: str,
) -> None:
    """List GHAS alerts matching the given filters."""
    from gitventory.models import CatalogEntity, CatalogMembership, GhasAlert, Repository
    from gitventory.store.query import build_alert_filters

    config = _load_config(ctx)
    filters = build_alert_filters(
        alert_type=alert_type, severity=severity, repo_id=repo_id, state=state
    )

    with create_store(config.store) as store:
        if catalog_entity:
            entity = _resolve_catalog_entity(store, catalog_entity)
            if entity is None:
                err_console.print(f"[red]Catalog entity not found:[/red] {catalog_entity}")
                sys.exit(1)
            memberships = store.query(
                CatalogMembership,
                {"catalog_entity_id": entity.id, "technical_entity_type": "repository"},
            )
            repo_ids = {m.technical_entity_id for m in memberships}
            all_alerts = store.query(GhasAlert, filters)
            results = [a for a in all_alerts if a.repo_id in repo_ids]
        else:
            results = store.query(GhasAlert, filters)

        # Build criticality lookup if weighted sort requested
        criticality_by_repo: dict[str, Optional[str]] = {}
        if sort_by == "weighted-priority":
            all_memberships = store.query(CatalogMembership, {"technical_entity_type": "repository"})
            for m in all_memberships:
                ce = store.get(CatalogEntity, m.catalog_entity_id)
                if ce and ce.criticality:
                    existing = criticality_by_repo.get(m.technical_entity_id)
                    if existing is None or _criticality_score(ce.criticality) > _criticality_score(existing):
                        criticality_by_repo[m.technical_entity_id] = ce.criticality

    if not results:
        console.print("[dim]No alerts found.[/dim]")
        return

    if sort_by == "weighted-priority":
        results.sort(
            key=lambda a: _weighted_priority(a.severity, criticality_by_repo.get(a.repo_id)),
            reverse=True,
        )

    cols = ["repo_id", "alert_type", "state", "severity", "secret_type", "rule_id", "created_at", "url"]
    if sort_by == "weighted-priority":
        # Annotate results with weighted_priority for display
        _output_alerts_with_priority(results, criticality_by_repo, output_fmt)
    else:
        _output(results, cols, output_fmt, "GHAS Alerts")


@query.command("mappings")
@click.option("--repo", "repo_id", default=None, help="Filter by repository ID or full_name slug.")
@click.option("--account", "account_id", default=None, help="Filter by cloud account ID.")
@click.option("--method", "detection_method", default=None, type=click.Choice(["oidc_workflow", "static_yaml"]), help="Detection method.")
@click.option("-o", "--output", "output_fmt", default="table", type=click.Choice(["table", "json"]), show_default=True)
@click.pass_context
def query_mappings(
    ctx: click.Context,
    repo_id: Optional[str],
    account_id: Optional[str],
    detection_method: Optional[str],
    output_fmt: str,
) -> None:
    """List deployment mappings (repo → cloud account links)."""
    from gitventory.models import DeploymentMapping
    from gitventory.store.query import build_mapping_filters

    config = _load_config(ctx)
    filters = build_mapping_filters(
        repo_id=repo_id, account_id=account_id, detection_method=detection_method
    )

    with create_store(config.store) as store:
        results = store.query(DeploymentMapping, filters)

    if not results:
        console.print("[dim]No deployment mappings found.[/dim]")
        return

    cols = ["repo_id", "target_type", "target_id", "deploy_method", "environment", "detection_method", "notes"]
    _output(results, cols, output_fmt, "Deployment Mappings")


@query.command("users")
@click.option("--team", "team_id", default=None, help="Filter by team ID (github:team:NNN or team:slug).")
@click.option("--repo", "repo_id", default=None, help="Filter by repository ID — shows collaborators on that repo.")
@click.option("--login", default=None, help="Filter by login (exact match).")
@click.option("-o", "--output", "output_fmt", default="table", type=click.Choice(["table", "json"]), show_default=True)
@click.pass_context
def query_users(
    ctx: click.Context,
    team_id: Optional[str],
    repo_id: Optional[str],
    login: Optional[str],
    output_fmt: str,
) -> None:
    """List discovered users, optionally filtered by team or repo."""
    from gitventory.models.user import User
    from gitventory.models.team_member import TeamMember
    from gitventory.models.repo_collaborator import RepoCollaborator

    config = _load_config(ctx)
    with create_store(config.store) as store:
        if team_id:
            # Resolve short form
            if not team_id.startswith(("team:", "github:")):
                team_id = f"team:{team_id}"
            member_rows = store.query(TeamMember, {"team_id": team_id})
            user_ids = {m.user_id for m in member_rows}
            all_users = [store.get(User, uid) for uid in user_ids]
            users = [u for u in all_users if u]
        elif repo_id:
            collab_rows = store.query(RepoCollaborator, {"repo_id": repo_id})
            user_ids = {c.user_id for c in collab_rows}
            all_users = [store.get(User, uid) for uid in user_ids]
            users = [u for u in all_users if u]
        else:
            filters: dict = {}
            if login:
                filters["login"] = login
            users = store.query(User, filters)

    if login and not team_id and not repo_id:
        users = [u for u in users if u.login == login]

    if not users:
        console.print("[dim]No users found.[/dim]")
        return

    cols = ["id", "login", "display_name", "email", "slack_handle", "provider"]
    _output(users, cols, output_fmt, "Users")


@query.command("repo-teams")
@click.argument("repo_id")
@click.option("--permission", default=None,
              type=click.Choice(["pull", "triage", "push", "maintain", "admin"]),
              help="Filter by permission level.")
@click.option("-o", "--output", "output_fmt", default="table", type=click.Choice(["table", "json"]), show_default=True)
@click.pass_context
def query_repo_teams(
    ctx: click.Context,
    repo_id: str,
    permission: Optional[str],
    output_fmt: str,
) -> None:
    """List teams assigned to a repository with their permission levels."""
    from gitventory.models.repo_team_assignment import RepoTeamAssignment
    from gitventory.models.team import Team

    config = _load_config(ctx)
    with create_store(config.store) as store:
        # Normalise repo_id
        if not repo_id.startswith("github:") and "/" in repo_id:
            repo = _resolve_repo(store, repo_id)
            if repo:
                repo_id = repo.id

        filters: dict = {"repo_id": repo_id}
        assignments = store.query(RepoTeamAssignment, filters)
        if permission:
            assignments = [a for a in assignments if a.permission == permission]

        # Enrich with team display names
        rows = []
        for a in assignments:
            team = store.get(Team, a.team_id)
            rows.append({
                "id": a.id,
                "repo_id": a.repo_id,
                "team_id": a.team_id,
                "team_name": (team.display_name if team else None) or a.team_id,
                "permission": a.permission,
                "org": a.org,
                "email": (team.email if team else None) or "",
                "slack": (team.slack_channel if team else None) or (team.contacts.get("slack_channel") if team else None) or "",
            })

    if not rows:
        console.print("[dim]No team assignments found for this repository.[/dim]")
        return

    if output_fmt == "json":
        console.print_json(json.dumps(rows))
        return

    import rich.table as rt
    tbl = rt.Table(title=f"Team Assignments for {repo_id}")
    tbl.add_column("Team")
    tbl.add_column("Permission")
    tbl.add_column("Email")
    tbl.add_column("Slack")
    tbl.add_column("Org")
    for row in rows:
        perm = row["permission"]
        perm_fmt = f"[bold red]{perm}[/bold red]" if perm == "admin" else (
            f"[yellow]{perm}[/yellow]" if perm == "maintain" else perm
        )
        tbl.add_row(
            row["team_name"], perm_fmt,
            row["email"] or "[dim]—[/dim]",
            row["slack"] or "[dim]—[/dim]",
            row["org"],
        )
    console.print(tbl)


@query.command("collaborators")
@click.argument("repo_id")
@click.option("--affiliation", default=None,
              type=click.Choice(["direct", "outside", "all"]),
              help="Filter by collaborator affiliation.")
@click.option("-o", "--output", "output_fmt", default="table", type=click.Choice(["table", "json"]), show_default=True)
@click.pass_context
def query_collaborators(
    ctx: click.Context,
    repo_id: str,
    affiliation: Optional[str],
    output_fmt: str,
) -> None:
    """List direct and outside collaborators on a repository."""
    from gitventory.models.repo_collaborator import RepoCollaborator
    from gitventory.models.user import User

    config = _load_config(ctx)
    with create_store(config.store) as store:
        if not repo_id.startswith("github:") and "/" in repo_id:
            repo = _resolve_repo(store, repo_id)
            if repo:
                repo_id = repo.id

        filters: dict = {"repo_id": repo_id}
        collabs = store.query(RepoCollaborator, filters)
        if affiliation:
            collabs = [c for c in collabs if c.affiliation == affiliation]

        rows = []
        for c in collabs:
            user = store.get(User, c.user_id)
            rows.append({
                "id": c.id,
                "repo_id": c.repo_id,
                "user_id": c.user_id,
                "login": (user.login if user else None) or c.user_id,
                "permission": c.permission,
                "affiliation": c.affiliation,
                "email": (user.email if user else None) or "",
                "slack_handle": (user.slack_handle if user else None) or "",
            })

    if not rows:
        console.print("[dim]No collaborators found for this repository.[/dim]")
        return

    if output_fmt == "json":
        console.print_json(json.dumps(rows))
        return

    import rich.table as rt
    tbl = rt.Table(title=f"Collaborators for {repo_id}")
    tbl.add_column("Login")
    tbl.add_column("Permission")
    tbl.add_column("Affiliation")
    tbl.add_column("Email")
    tbl.add_column("Slack")
    for row in rows:
        tbl.add_row(
            row["login"], row["permission"], row["affiliation"],
            row["email"] or "[dim]—[/dim]",
            row["slack_handle"] or "[dim]—[/dim]",
        )
    console.print(tbl)


# ---------------------------------------------------------------------------
# catalog group
# ---------------------------------------------------------------------------

@main.group()
def catalog() -> None:
    """Catalog management — sync and query the organizational meta-model."""


@catalog.command("sync")
@click.option("--clear", is_flag=True, help="Delete all existing memberships before re-evaluating.")
@click.option("-v", "--verbose", is_flag=True, help="Enable DEBUG logging.")
@click.pass_context
def catalog_sync(ctx: click.Context, clear: bool, verbose: bool) -> None:
    """Evaluate catalog matchers and update membership links in the store.

    Re-runs the matcher step without re-fetching data from GitHub or other
    adapters.  Use --clear to wipe all existing memberships first (full re-hydration).
    """
    _setup_logging(verbose)
    config = _load_config(ctx)

    if not config.catalog.file:
        err_console.print(
            "[red]No catalog file configured.[/red] "
            "Add [bold]catalog.file[/bold] to config.yaml."
        )
        sys.exit(1)

    from gitventory.catalog.sync import CatalogSyncer
    with create_store(config.store) as store:
        syncer = CatalogSyncer(config.catalog.file, store)
        try:
            counts = syncer.sync(clear=clear)
        except FileNotFoundError as exc:
            err_console.print(f"[red]Catalog file not found:[/red] {exc}")
            sys.exit(1)

    if clear:
        console.print("[yellow]Memberships cleared and rebuilt.[/yellow]")
    console.print(
        f"[green]Catalog sync complete:[/green] "
        f"{counts['entities']} entities, {counts['memberships']} memberships"
    )


# ---------------------------------------------------------------------------
# ownership group
# ---------------------------------------------------------------------------

@main.group()
def ownership() -> None:
    """Ownership management — assign repo owners from GitHub team membership."""


@ownership.command("sync")
@click.option("--force", is_flag=True, help="Overwrite existing owning_team_id assignments.")
@click.option("-v", "--verbose", is_flag=True, help="Enable DEBUG logging.")
@click.pass_context
def ownership_sync(ctx: click.Context, force: bool, verbose: bool) -> None:
    """Assign owning_team_id on repositories from GitHub team membership.

    Reads teams from the store (as loaded by static_yaml), resolves their GitHub
    team identities, fetches the repository list for each team, and patches
    owning_team_id on repos that don't already have an owner (unless --force).

    Precedence: owning_team_id already set in YAML or catalog is never
    overwritten unless --force is passed.
    """
    _setup_logging(verbose)
    config = _load_config(ctx)

    if not config.adapters.github or not config.adapters.github.enabled:
        err_console.print(
            "[red]GitHub adapter not configured or disabled.[/red] "
            "Ownership sync requires the GitHub adapter."
        )
        sys.exit(1)

    from gitventory.ownership.sync import OwnershipSyncer
    with create_store(config.store) as store:
        syncer = OwnershipSyncer(config.adapters.github, store)
        try:
            counts = syncer.sync(force=force)
        except Exception as exc:
            err_console.print(f"[red]Ownership sync failed:[/red] {exc}")
            sys.exit(1)

    if force:
        console.print("[yellow]Ownership sync ran with --force (existing assignments overwritten).[/yellow]")
    console.print(
        f"[green]Ownership sync complete:[/green] "
        f"{counts['repos_updated']} repos updated across {counts['teams_processed']} teams"
    )


# ---------------------------------------------------------------------------
# sync — re-run all post-collect steps without touching adapters
# ---------------------------------------------------------------------------

@main.command("sync")
@click.option("--catalog/--no-catalog", default=True, show_default=True,
              help="Run catalog sync.")
@click.option("--ownership/--no-ownership", default=True, show_default=True,
              help="Run ownership sync.")
@click.option("--teams/--no-teams", default=True, show_default=True,
              help="Run team enrichment.")
@click.option("--users/--no-users", default=True, show_default=True,
              help="Run user enrichment.")
@click.option("-v", "--verbose", is_flag=True, help="Enable DEBUG logging.")
@click.pass_context
def sync_cmd(ctx: click.Context, catalog: bool, ownership: bool, teams: bool, users: bool, verbose: bool) -> None:
    """Re-apply YAML enrichment without re-collecting from adapters.

    Runs catalog sync, ownership sync, team enrichment, and user enrichment
    in the correct order using data already in the store.  Use --no-X flags
    to skip individual steps.
    """
    _setup_logging(verbose)
    config = _load_config(ctx)

    results: dict = {}

    with create_store(config.store) as store:
        if catalog:
            if config.catalog.file:
                from gitventory.catalog.sync import CatalogSyncer
                try:
                    syncer = CatalogSyncer(config.catalog.file, store)
                    counts = syncer.sync(clear=False)
                    results["catalog"] = counts
                except Exception as exc:
                    err_console.print(f"[red]Catalog sync failed:[/red] {exc}")
            else:
                console.print("[dim]Catalog sync skipped — no catalog.file configured.[/dim]")

        if ownership:
            if config.adapters.github and config.adapters.github.enabled:
                from gitventory.ownership.sync import OwnershipSyncer
                try:
                    syncer = OwnershipSyncer(config.adapters.github, store)
                    counts = syncer.sync(force=False)
                    results["ownership"] = counts
                except Exception as exc:
                    err_console.print(f"[red]Ownership sync failed:[/red] {exc}")
            else:
                console.print("[dim]Ownership sync skipped — GitHub adapter not configured.[/dim]")

        if teams:
            from gitventory.ownership.team_enrichment import TeamEnrichmentSyncer
            try:
                syncer = TeamEnrichmentSyncer(store)
                counts = syncer.sync()
                results["teams"] = counts
            except Exception as exc:
                err_console.print(f"[red]Team enrichment failed:[/red] {exc}")

        if users:
            users_file = (
                config.adapters.static_yaml.users_file
                if config.adapters.static_yaml
                else None
            )
            if users_file:
                from gitventory.ownership.user_enrichment import UserEnrichmentSyncer
                try:
                    syncer = UserEnrichmentSyncer(users_file, store)
                    counts = syncer.sync()
                    results["users"] = counts
                except Exception as exc:
                    err_console.print(f"[red]User enrichment failed:[/red] {exc}")
            else:
                console.print("[dim]User enrichment skipped — no static_yaml.users_file configured.[/dim]")

    # Summary
    from rich.table import Table
    table = Table(title="Sync results", show_header=True)
    table.add_column("Step")
    table.add_column("Result", style="green")

    if "catalog" in results:
        c = results["catalog"]
        table.add_row("Catalog sync", f"{c['entities']} entities, {c['memberships']} memberships")
    if "ownership" in results:
        c = results["ownership"]
        table.add_row("Ownership sync", f"{c['repos_updated']} repos updated across {c['teams_processed']} teams")
    if "teams" in results:
        c = results["teams"]
        table.add_row("Team enrichment", f"{c['teams_enriched']} teams enriched")
    if "users" in results:
        c = results["users"]
        table.add_row("User enrichment", f"{c['users_enriched']} enriched, {c['unmatched_refs']} unmatched")

    if results:
        console.print(table)
    else:
        console.print("[yellow]No sync steps ran — check your config or use --help.[/yellow]")


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

@main.group()
def show() -> None:
    """Show full detail for a single entity."""


@show.command("repo")
@click.argument("repo_id")
@click.pass_context
def show_repo(ctx: click.Context, repo_id: str) -> None:
    """Show full details for a repository (accepts stable ID or org/repo slug)."""
    from gitventory.models import Repository

    config = _load_config(ctx)
    with create_store(config.store) as store:
        entity = _resolve_repo(store, repo_id)

    if entity is None:
        err_console.print(f"[red]Repository not found:[/red] {repo_id}")
        sys.exit(1)

    _print_detail(entity)


@show.command("catalog")
@click.argument("entity_id")
@click.pass_context
def show_catalog(ctx: click.Context, entity_id: str) -> None:
    """Show full details for a catalog entity including linked repos and accounts.

    Accepts a stable ID (catalog:service:checkout-api), a provider_id
    (service:checkout-api), or an entity slug (checkout-api).
    """
    from gitventory.models import CatalogEntity, CatalogMembership, Repository, CloudAccount

    config = _load_config(ctx)
    with create_store(config.store) as store:
        entity = _resolve_catalog_entity(store, entity_id)
        if entity is None:
            err_console.print(f"[red]Catalog entity not found:[/red] {entity_id}")
            sys.exit(1)

        _print_detail(entity)

        # Show linked technical entities
        memberships = store.query(CatalogMembership, {"catalog_entity_id": entity.id})
        if memberships:
            repo_ids = [m.technical_entity_id for m in memberships if m.technical_entity_type == "repository"]
            account_ids = [m.technical_entity_id for m in memberships if m.technical_entity_type == "cloud_account"]

            if repo_ids:
                repos = [store.get(Repository, rid) for rid in repo_ids]
                repos = [r for r in repos if r]
                if repos:
                    import rich.table as rt
                    tbl = rt.Table(title=f"Linked Repositories ({len(repos)})")
                    for col in ["full_name", "language", "is_archived", "open_secret_alerts", "open_dependabot_alerts"]:
                        tbl.add_column(col.replace("_", " ").title())
                    for r in repos:
                        tbl.add_row(
                            r.full_name or "",
                            r.language or "[dim]—[/dim]",
                            "[yellow]archived[/yellow]" if r.is_archived else "no",
                            f"[red]{r.open_secret_alerts}[/red]" if r.open_secret_alerts else "0",
                            f"[red]{r.open_dependabot_alerts}[/red]" if r.open_dependabot_alerts else "0",
                        )
                    console.print(tbl)

            if account_ids:
                accounts_list = [store.get(CloudAccount, aid) for aid in account_ids]
                accounts_list = [a for a in accounts_list if a]
                if accounts_list:
                    import rich.table as rt
                    tbl = rt.Table(title=f"Linked Cloud Accounts ({len(accounts_list)})")
                    for col in ["id", "provider", "name", "environment"]:
                        tbl.add_column(col.replace("_", " ").title())
                    for a in accounts_list:
                        tbl.add_row(a.id, a.provider, a.name or "", a.environment or "[dim]—[/dim]")
                    console.print(tbl)
        else:
            console.print("[dim]No linked repositories or accounts.[/dim]")


@show.command("account")
@click.argument("account_id")
@click.pass_context
def show_account(ctx: click.Context, account_id: str) -> None:
    """Show full details for a cloud account including deploying repos, responsible teams,
    and key contacts.

    Accepts a stable ID (aws:123456789012) or a bare account ID (123456789012).
    """
    from gitventory.models import CloudAccount, DeploymentMapping, Repository
    from gitventory.models.repo_team_assignment import RepoTeamAssignment
    from gitventory.models.team_member import TeamMember
    from gitventory.models.team import Team
    from gitventory.models.user import User
    import rich.table as rt

    config = _load_config(ctx)
    if not account_id.startswith(("aws:", "azure:")):
        account_id = f"aws:{account_id}"

    with create_store(config.store) as store:
        entity = store.get(CloudAccount, account_id)
        if entity is None:
            err_console.print(f"[red]Account not found:[/red] {account_id}")
            sys.exit(1)

        _print_detail(entity)

        # Deploying repositories
        mappings = store.query(DeploymentMapping, {"target_id": account_id})
        if not mappings:
            console.print("[dim]No known deployment mappings to this account.[/dim]")
            return

        repo_ids = list({m.repo_id for m in mappings if m.repo_id})
        repos = [store.get(Repository, rid) for rid in repo_ids]
        repos = [r for r in repos if r]

        if repos:
            tbl = rt.Table(title=f"Deploying Repositories ({len(repos)})")
            tbl.add_column("Repository")
            tbl.add_column("Method")
            tbl.add_column("Environment")
            for r in repos:
                rel_mappings = [m for m in mappings if m.repo_id == r.id]
                methods = ", ".join({m.deploy_method or "—" for m in rel_mappings})
                envs = ", ".join({m.environment or "—" for m in rel_mappings})
                tbl.add_row(r.full_name or r.id, methods, envs)
            console.print(tbl)

        # Responsible teams (admin/maintain permission on deploying repos)
        responsible_team_ids: set[str] = set()
        for r in repos:
            rtas = store.query(RepoTeamAssignment, {"repo_id": r.id})
            for rta in rtas:
                if rta.permission in ("admin", "maintain"):
                    responsible_team_ids.add(rta.team_id)

        if responsible_team_ids:
            tbl2 = rt.Table(title=f"Responsible Teams ({len(responsible_team_ids)})")
            tbl2.add_column("Team")
            tbl2.add_column("Email")
            tbl2.add_column("Slack")
            for team_id in sorted(responsible_team_ids):
                team = store.get(Team, team_id)
                if team is None:
                    tbl2.add_row(team_id, "[dim]—[/dim]", "[dim]—[/dim]")
                    continue
                email = team.email or team.contacts.get("email") or "[dim]—[/dim]"
                slack = team.slack_channel or team.contacts.get("slack_channel") or "[dim]—[/dim]"
                tbl2.add_row(team.display_name or team_id, email, slack)
            console.print(tbl2)

            # Key contacts (team maintainers with email)
            contacts: list[tuple[str, str, str, str]] = []  # (team_name, login, role, email)
            for team_id in sorted(responsible_team_ids):
                team = store.get(Team, team_id)
                team_name = (team.display_name if team else None) or team_id
                members = store.query(TeamMember, {"team_id": team_id})
                for m in members:
                    if m.role == "maintainer":
                        user = store.get(User, m.user_id)
                        login = (user.login if user else None) or m.user_id
                        email = (user.email if user else None) or "[dim]—[/dim]"
                        contacts.append((team_name, login, m.role, email))

            if contacts:
                tbl3 = rt.Table(title=f"Key Contacts ({len(contacts)})")
                tbl3.add_column("Team")
                tbl3.add_column("Login")
                tbl3.add_column("Role")
                tbl3.add_column("Email")
                for team_name, login, role, email in contacts:
                    tbl3.add_row(team_name, login, role, email)
                console.print(tbl3)


@show.command("team")
@click.argument("team_id")
@click.pass_context
def show_team(ctx: click.Context, team_id: str) -> None:
    """Show full details for a team including identity mappings and linked repositories."""
    from gitventory.models import Repository, Team

    config = _load_config(ctx)
    if not team_id.startswith(("team:", "github:team:")):
        team_id = f"team:{team_id}"

    with create_store(config.store) as store:
        entity = store.get(Team, team_id)
        if entity is None:
            err_console.print(f"[red]Team not found:[/red] {team_id}")
            sys.exit(1)

        _print_detail(entity)

        # Show external identity mappings
        if entity.identities:
            import rich.table as rt
            tbl = rt.Table(title=f"External Identities ({len(entity.identities)})")
            tbl.add_column("Provider")
            tbl.add_column("Value")
            tbl.add_column("Metadata")
            for ident in entity.identities:
                meta = json.dumps(ident.metadata) if ident.metadata else "[dim]—[/dim]"
                tbl.add_row(ident.provider, ident.value, meta)
            console.print(tbl)

        # Show contact channels
        if entity.contacts:
            import rich.table as rt
            tbl = rt.Table(title=f"Contact Channels ({len(entity.contacts)})")
            tbl.add_column("Channel")
            tbl.add_column("Value")
            for channel, value in entity.contacts.items():
                tbl.add_row(channel, value)
            console.print(tbl)

        # Show team members (available for GitHub-discovered teams)
        from gitventory.models.team_member import TeamMember
        from gitventory.models.user import User
        members = store.query(TeamMember, {"team_id": entity.id})
        if members:
            import rich.table as rt
            tbl = rt.Table(title=f"Team Members ({len(members)})")
            tbl.add_column("Login")
            tbl.add_column("Role")
            tbl.add_column("Email")
            for m in sorted(members, key=lambda x: (x.role, x.user_id)):
                user = store.get(User, m.user_id)
                login = (user.login if user else None) or m.user_id
                email = (user.email if user else None) or "[dim]—[/dim]"
                role_fmt = f"[bold]{m.role}[/bold]" if m.role == "maintainer" else m.role
                tbl.add_row(login, role_fmt, email)
            console.print(tbl)

        # Show linked repositories (repos where owning_team_id == team.id)
        repos = store.query(Repository, {"owning_team_id": entity.id})
        if repos:
            import rich.table as rt
            tbl = rt.Table(title=f"Owned Repositories ({len(repos)})")
            for col in ["full_name", "language", "is_archived", "open_secret_alerts", "open_dependabot_alerts"]:
                tbl.add_column(col.replace("_", " ").title())
            for r in repos:
                tbl.add_row(
                    r.full_name or "",
                    r.language or "[dim]—[/dim]",
                    "[yellow]archived[/yellow]" if r.is_archived else "no",
                    f"[red]{r.open_secret_alerts}[/red]" if r.open_secret_alerts else "0",
                    f"[red]{r.open_dependabot_alerts}[/red]" if r.open_dependabot_alerts else "0",
                )
            console.print(tbl)
        else:
            console.print("[dim]No owned repositories.[/dim]")


# ---------------------------------------------------------------------------
# adapters
# ---------------------------------------------------------------------------

@main.group()
def adapters() -> None:
    """Adapter management."""


@adapters.command("list")
@click.pass_context
def adapters_list(ctx: click.Context) -> None:
    """List all registered adapters and their enabled status."""
    import gitventory.adapters  # noqa: F401 — triggers registration
    from gitventory.registry import get_registry

    config = _load_config(ctx)
    registry = get_registry()
    enabled_map = config.adapters.enabled_adapters()

    table = Table(title="Registered Adapters")
    table.add_column("Name", style="bold")
    table.add_column("Class")
    table.add_column("Enabled")

    for name, cls in sorted(registry.items()):
        enabled = name in enabled_map
        table.add_row(
            name,
            cls.__qualname__,
            "[green]yes[/green]" if enabled else "[dim]no[/dim]",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------

@main.group()
def store() -> None:
    """Store management."""


@store.command("init")
@click.pass_context
def store_init(ctx: click.Context) -> None:
    """Initialise the store schema (safe to run multiple times)."""
    config = _load_config(ctx)
    with create_store(config.store) as s:
        s.init_schema()
    console.print("[green]Store schema initialised.[/green]")


@store.command("status")
@click.pass_context
def store_status(ctx: click.Context) -> None:
    """Show entity counts and last collection times."""
    config = _load_config(ctx)
    with create_store(config.store) as s:
        summary = s.status_summary()  # type: ignore[attr-defined]

    table = Table(title="Store Status — Entity Counts")
    table.add_column("Entity Type", style="bold")
    table.add_column("Count", justify="right")
    for entity_name, count in sorted(summary["entity_counts"].items()):
        table.add_row(entity_name, str(count))
    console.print(table)

    if summary["last_collected"]:
        table2 = Table(title="Last Successful Collection")
        table2.add_column("Adapter", style="bold")
        table2.add_column("Finished At")
        for adapter_name, ts in sorted(summary["last_collected"].items()):
            table2.add_row(adapter_name, str(ts))
        console.print(table2)


@store.command("export")
@click.argument("path")
@click.pass_context
def store_export(ctx: click.Context, path: str) -> None:
    """Export the full store contents to a JSON file."""
    import datetime as dt

    config = _load_config(ctx)
    with create_store(config.store) as s:
        data = s.export_all()  # type: ignore[attr-defined]

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _default(obj):
        if isinstance(obj, (dt.datetime, dt.date)):
            return obj.isoformat()
        raise TypeError(f"Not serialisable: {type(obj)}")

    out_path.write_text(json.dumps(data, indent=2, default=_default), encoding="utf-8")
    total = sum(len(v) for v in data.values())
    console.print(f"[green]Exported {total} records to {out_path}[/green]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(ctx: click.Context):
    path = ctx.find_root().obj.get("config_path", "config.yaml")
    try:
        return load_config(path)
    except FileNotFoundError:
        err_console.print(
            f"[red]Config file not found:[/red] {path}\n"
            f"Copy config.example.yaml to {path} and fill in your values."
        )
        sys.exit(1)
    except KeyError as e:
        err_console.print(f"[red]Config error:[/red] {e}")
        sys.exit(1)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _output(results: list, cols: list[str], fmt: str, title: str) -> None:
    if fmt == "json":
        output_data = []
        for obj in results:
            row = {}
            for col in cols:
                val = getattr(obj, col, None)
                row[col] = str(val) if val is not None else None
            output_data.append(row)
        console.print_json(json.dumps(output_data))
        return

    table = Table(title=f"{title} ({len(results)})")
    for col in cols:
        table.add_column(col.replace("_", " ").title())

    for obj in results:
        row_vals = []
        for col in cols:
            val = getattr(obj, col, None)
            if val is None:
                row_vals.append("[dim]—[/dim]")
            elif col == "is_archived" and val:
                row_vals.append("[yellow]archived[/yellow]")
            elif col in ("open_secret_alerts", "open_code_scanning_alerts", "open_dependabot_alerts") and val > 0:
                row_vals.append(f"[red]{val}[/red]")
            else:
                row_vals.append(str(val))
        table.add_row(*row_vals)

    console.print(table)


def _resolve_catalog_entity(store, entity_id: str):
    """Accept stable ID, provider_id (type:slug), or bare slug."""
    from gitventory.models import CatalogEntity

    # Try stable ID first (catalog:type:slug)
    entity = store.get(CatalogEntity, entity_id)
    if entity:
        return entity

    # Try provider_id (type:slug) — query by provider_id
    results = store.query(CatalogEntity, {"provider_id": entity_id})
    if results:
        return results[0]

    # Try bare slug — match against the last component of provider_id
    # e.g. "checkout-api" matches "service:checkout-api"
    all_entities = store.query(CatalogEntity, {})
    for e in all_entities:
        # provider_id is "{type_id}:{entity_slug}"
        slug = e.provider_id.split(":", 1)[-1] if ":" in e.provider_id else e.provider_id
        if slug == entity_id:
            return e

    return None


def _resolve_repo(store, repo_id: str):
    """Accept either a stable ID (``github:NNN``) or a full_name slug (``org/repo``)."""
    from gitventory.models import Repository

    # Try stable ID first
    entity = store.get(Repository, repo_id)
    if entity:
        return entity

    # Fall back to full_name slug query
    results = store.query(Repository, {"full_name": repo_id})
    return results[0] if results else None


_SEVERITY_SCORES = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_CRITICALITY_WEIGHTS = {"critical": 2.0, "high": 1.5, "medium": 1.0, "low": 0.5}


def _criticality_score(criticality: Optional[str]) -> float:
    return _CRITICALITY_WEIGHTS.get(criticality or "", 1.0)


def _weighted_priority(severity: Optional[str], criticality: Optional[str]) -> float:
    """Compute severity × criticality_weight.

    Original alert severity is never mutated — this is computed at display time only.
    A repo not linked to any catalog entity defaults to weight 1.0 (neutral).
    """
    s = _SEVERITY_SCORES.get(severity or "", 0)
    w = _CRITICALITY_WEIGHTS.get(criticality or "", 1.0)
    return s * w


def _output_alerts_with_priority(
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
            wp = _weighted_priority(a.severity, criticality_by_repo.get(a.repo_id))
            row = {
                "repo_id": a.repo_id,
                "alert_type": a.alert_type,
                "state": a.state,
                "severity": a.severity,
                "weighted_priority": wp,
                "secret_type": a.secret_type,
                "rule_id": a.rule_id,
                "created_at": str(a.created_at),
            }
            output_data.append(row)
        console.print_json(json.dumps(output_data))
        return

    table = Table(title=f"GHAS Alerts — sorted by weighted priority ({len(alerts)})")
    for col in cols:
        table.add_column(col.replace("_", " ").title())

    for a in alerts:
        wp = _weighted_priority(a.severity, criticality_by_repo.get(a.repo_id))
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


def _print_detail(entity) -> None:
    """Print all fields of an entity as a two-column table."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold dim")
    table.add_column("Value")

    for field_name, value in entity.model_dump().items():
        if field_name == "raw":
            continue  # Skip raw payload in detail view
        display = json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value)
        table.add_row(field_name, display)

    console.print(table)
