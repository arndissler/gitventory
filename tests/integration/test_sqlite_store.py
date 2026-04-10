"""Integration tests for SQLiteStore — use in-memory SQLite (:memory:)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gitventory.models import CloudAccount, DeploymentMapping, GhasAlert, Repository, Team
from gitventory.store.sqlite import SQLiteStore

NOW = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = SQLiteStore(str(tmp_path / "test.db"))
    yield s
    s.close()


def make_repo(id_suffix="12345678", full_name="my-org/my-repo", **kwargs) -> Repository:
    defaults = dict(
        id=f"github:{id_suffix}",
        provider_id=id_suffix,
        provider="github",
        source_adapter="github",
        collected_at=NOW,
        org="my-org",
        name="my-repo",
        full_name=full_name,
        url=f"https://github.com/{full_name}",
        default_branch="main",
    )
    defaults.update(kwargs)
    return Repository(**defaults)


def make_account(account_id="123456789012", **kwargs) -> CloudAccount:
    return CloudAccount(
        id=f"aws:{account_id}",
        provider_id=account_id,
        provider="aws",
        source_adapter="static_yaml",
        collected_at=NOW,
        name="prod",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Basic upsert / get
# ---------------------------------------------------------------------------

def test_upsert_and_get_repository(store):
    repo = make_repo()
    store.upsert(repo)
    fetched = store.get(Repository, "github:12345678")
    assert fetched is not None
    assert fetched.id == "github:12345678"
    assert fetched.full_name == "my-org/my-repo"


def test_upsert_updates_mutable_field(store):
    """Re-upserting with a new full_name (rename) must update the row."""
    repo = make_repo()
    store.upsert(repo)

    renamed = repo.model_copy(update={"full_name": "my-org/renamed-repo"})
    store.upsert(renamed)

    fetched = store.get(Repository, "github:12345678")
    assert fetched.full_name == "my-org/renamed-repo"
    assert fetched.id == "github:12345678"  # stable ID unchanged


def test_get_returns_none_for_missing(store):
    assert store.get(Repository, "github:99999999") is None


def test_upsert_cloud_account(store):
    account = make_account()
    store.upsert(account)
    fetched = store.get(CloudAccount, "aws:123456789012")
    assert fetched is not None
    assert fetched.provider == "aws"


def test_upsert_team(store):
    team = Team(
        id="team:platform-eng",
        provider_id="platform-eng",
        source_adapter="static_yaml",
        collected_at=NOW,
        display_name="Platform Engineering",
    )
    store.upsert(team)
    fetched = store.get(Team, "team:platform-eng")
    assert fetched.display_name == "Platform Engineering"


# ---------------------------------------------------------------------------
# upsert_many
# ---------------------------------------------------------------------------

def test_upsert_many_returns_count(store):
    repos = [make_repo(id_suffix=str(i), full_name=f"my-org/repo-{i}") for i in range(5)]
    count = store.upsert_many(iter(repos))
    assert count == 5


def test_count(store):
    repos = [make_repo(id_suffix=str(i), full_name=f"my-org/repo-{i}") for i in range(3)]
    store.upsert_many(iter(repos))
    assert store.count(Repository) == 3


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def test_query_by_org(store):
    repo_a = make_repo(id_suffix="1", full_name="org-a/repo", org="org-a")
    repo_b = make_repo(id_suffix="2", full_name="org-b/repo", org="org-b")
    store.upsert_many(iter([repo_a, repo_b]))

    results = store.query(Repository, {"org": "org-a"})
    assert len(results) == 1
    assert results[0].org == "org-a"


def test_query_by_is_archived(store):
    active = make_repo(id_suffix="1", full_name="my-org/active", is_archived=False)
    archived = make_repo(id_suffix="2", full_name="my-org/archived", is_archived=True)
    store.upsert_many(iter([active, archived]))

    results = store.query(Repository, {"is_archived": False})
    assert len(results) == 1
    assert results[0].id == "github:1"


def test_query_has_open_alerts(store):
    clean = make_repo(id_suffix="1", full_name="my-org/clean")
    vulnerable = make_repo(id_suffix="2", full_name="my-org/vulnerable", open_secret_alerts=2)
    store.upsert_many(iter([clean, vulnerable]))

    results = store.query(Repository, {"has_open_alerts": True})
    assert len(results) == 1
    assert results[0].id == "github:2"


def test_query_returns_empty_list_on_no_match(store):
    store.upsert(make_repo())
    results = store.query(Repository, {"org": "nonexistent-org"})
    assert results == []


# ---------------------------------------------------------------------------
# Deployment mappings
# ---------------------------------------------------------------------------

def test_deployment_mapping_round_trip(store):
    mapping = DeploymentMapping(
        id="github:12345678::aws:123456789012::prod",
        provider_id="github:12345678::aws:123456789012::prod",
        source_adapter="github",
        collected_at=NOW,
        repo_id="github:12345678",
        target_type="cloud_account",
        target_id="aws:123456789012",
        deploy_method="github_actions_oidc",
        environment="prod",
        detection_method="oidc_workflow",
        notes="Role: arn:aws:iam::123456789012:role/deploy, Workflow: deploy.yml",
    )
    store.upsert(mapping)

    results = store.query(DeploymentMapping, {"repo_id": "github:12345678"})
    assert len(results) == 1
    assert results[0].detection_method == "oidc_workflow"


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def test_record_and_retrieve_collection_run(store):
    from datetime import timedelta
    started = NOW
    finished = NOW + timedelta(seconds=5)
    store.record_collection_run("github", started, finished, 42, "success")

    last = store.last_collected_at("github")
    assert last is not None


def test_last_collected_at_returns_none_for_unknown_adapter(store):
    assert store.last_collected_at("unknown_adapter") is None


# ---------------------------------------------------------------------------
# Status summary
# ---------------------------------------------------------------------------

def test_status_summary(store):
    store.upsert(make_repo())
    store.upsert(make_account())
    summary = store.status_summary()
    assert summary["entity_counts"]["Repository"] == 1
    assert summary["entity_counts"]["CloudAccount"] == 1
