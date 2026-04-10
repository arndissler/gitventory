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
    from gitventory.models import Repository
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
        results = store.query(Repository, filters)

    if not results:
        console.print("[dim]No repositories found.[/dim]")
        return

    default_fields = ["id", "full_name", "provider", "language", "visibility",
                      "is_archived", "last_push_at", "open_secret_alerts",
                      "open_code_scanning_alerts", "open_dependabot_alerts", "owning_team_id"]
    cols = fields.split(",") if fields else default_fields

    _output(results, cols, output_fmt, "Repositories")


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
@click.option("--state", default="open", type=click.Choice(["open", "dismissed", "fixed", "all"]), show_default=True)
@click.option("-o", "--output", "output_fmt", default="table", type=click.Choice(["table", "json"]), show_default=True)
@click.pass_context
def query_alerts(
    ctx: click.Context,
    alert_type: Optional[str],
    severity: Optional[str],
    repo_id: Optional[str],
    state: str,
    output_fmt: str,
) -> None:
    """List GHAS alerts matching the given filters."""
    from gitventory.models import GhasAlert
    from gitventory.store.query import build_alert_filters

    config = _load_config(ctx)
    filters = build_alert_filters(
        alert_type=alert_type, severity=severity, repo_id=repo_id, state=state
    )

    with create_store(config.store) as store:
        results = store.query(GhasAlert, filters)

    if not results:
        console.print("[dim]No alerts found.[/dim]")
        return

    cols = ["id", "repo_id", "alert_type", "state", "severity", "secret_type", "rule_id", "created_at", "url"]
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


@show.command("account")
@click.argument("account_id")
@click.pass_context
def show_account(ctx: click.Context, account_id: str) -> None:
    """Show full details for a cloud account (accepts stable ID or bare account ID)."""
    from gitventory.models import CloudAccount

    config = _load_config(ctx)
    # Normalise: accept "123456789012" as well as "aws:123456789012"
    if not account_id.startswith(("aws:", "azure:")):
        account_id = f"aws:{account_id}"

    with create_store(config.store) as store:
        entity = store.get(CloudAccount, account_id)

    if entity is None:
        err_console.print(f"[red]Account not found:[/red] {account_id}")
        sys.exit(1)

    _print_detail(entity)


@show.command("team")
@click.argument("team_id")
@click.pass_context
def show_team(ctx: click.Context, team_id: str) -> None:
    """Show full details for a team."""
    from gitventory.models import Team

    config = _load_config(ctx)
    if not team_id.startswith("team:"):
        team_id = f"team:{team_id}"

    with create_store(config.store) as store:
        entity = store.get(Team, team_id)

    if entity is None:
        err_console.print(f"[red]Team not found:[/red] {team_id}")
        sys.exit(1)

    _print_detail(entity)


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
