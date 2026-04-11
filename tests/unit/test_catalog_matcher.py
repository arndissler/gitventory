"""Unit tests for CatalogMatcher — rule evaluation against in-memory store data."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from gitventory.catalog.matcher import CatalogMatcher
from gitventory.catalog.schema import CatalogEntityEntry, CatalogFile
from gitventory.models.cloud_account import CloudAccount
from gitventory.models.repository import Repository

NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_repo(
    full_name: str,
    topics: list[str] | None = None,
    custom_properties: dict | None = None,
    repo_id: int | None = None,
) -> Repository:
    parts = full_name.split("/")
    org, name = (parts[0], parts[1]) if len(parts) == 2 else ("org", parts[0])
    numeric_id = repo_id or abs(hash(full_name)) % 10_000
    return Repository(
        id=f"github:{numeric_id}",
        provider_id=str(numeric_id),
        provider="github",
        source_adapter="github",
        collected_at=NOW,
        org=org,
        name=name,
        full_name=full_name,
        url=f"https://github.com/{full_name}",
        raw={"id": numeric_id, "full_name": full_name, "custom_properties": custom_properties or {}},
        topics=topics or [],
    )


def _make_account(
    account_id: str,
    provider: str = "aws",
    environment: str | None = None,
    name: str = "test-account",
    tags: dict | None = None,
) -> CloudAccount:
    return CloudAccount(
        id=account_id,
        provider_id=account_id.split(":", 1)[-1],
        provider=provider,
        source_adapter="static_yaml",
        collected_at=NOW,
        name=name,
        environment=environment,
        tags=tags or {},
    )


def _make_store(repos: list[Repository], accounts: list[CloudAccount]) -> MagicMock:
    store = MagicMock()
    store.query.side_effect = lambda entity_type, filters: (
        repos if entity_type is Repository else accounts
    )
    return store


def _parse_entity(raw: dict) -> CatalogEntityEntry:
    """Parse a single entity entry through the full schema for realism."""
    cfg = CatalogFile(**{
        "catalog": {
            "entity_types": [{"id": raw["type"]}],
            "entities": [raw],
        }
    })
    return cfg.catalog.entities[0]


# ---------------------------------------------------------------------------
# full_name matchers
# ---------------------------------------------------------------------------

def test_exact_full_name_match():
    repo = _make_repo("my-org/checkout-api")
    store = _make_store([repo, _make_repo("my-org/payments")], [])
    matcher = CatalogMatcher(store)

    entry = _parse_entity({
        "id": "svc", "type": "service",
        "matchers": {"repos": [{"full_name": "my-org/checkout-api"}]},
    })
    memberships = matcher.evaluate(entry, "catalog:service:svc", NOW)
    assert len(memberships) == 1
    assert memberships[0].technical_entity_id == repo.id
    assert "full_name" in memberships[0].matched_by


def test_glob_full_name_match():
    repos = [_make_repo("my-org/checkout-api"), _make_repo("my-org/checkout-worker"), _make_repo("my-org/payments")]
    store = _make_store(repos, [])
    matcher = CatalogMatcher(store)

    entry = _parse_entity({
        "id": "svc", "type": "service",
        "matchers": {"repos": [{"full_name": "my-org/checkout-*"}]},
    })
    memberships = matcher.evaluate(entry, "catalog:service:svc", NOW)
    matched_names = {m.technical_entity_id for m in memberships}
    assert repos[0].id in matched_names
    assert repos[1].id in matched_names
    assert repos[2].id not in matched_names


def test_no_match_returns_empty():
    store = _make_store([_make_repo("my-org/other")], [])
    matcher = CatalogMatcher(store)

    entry = _parse_entity({
        "id": "svc", "type": "service",
        "matchers": {"repos": [{"full_name": "my-org/missing"}]},
    })
    memberships = matcher.evaluate(entry, "catalog:service:svc", NOW)
    assert memberships == []


# ---------------------------------------------------------------------------
# topics matchers
# ---------------------------------------------------------------------------

def test_topics_any_match():
    repo_a = _make_repo("org/a", topics=["checkout", "api"])
    repo_b = _make_repo("org/b", topics=["payments"])
    repo_c = _make_repo("org/c", topics=["infra"])
    store = _make_store([repo_a, repo_b, repo_c], [])
    matcher = CatalogMatcher(store)

    entry = _parse_entity({
        "id": "svc", "type": "service",
        "matchers": {"repos": [{"topics": {"any": ["checkout", "payments"]}}]},
    })
    memberships = matcher.evaluate(entry, "catalog:service:svc", NOW)
    ids = {m.technical_entity_id for m in memberships}
    assert repo_a.id in ids
    assert repo_b.id in ids
    assert repo_c.id not in ids


# ---------------------------------------------------------------------------
# github_property matchers
# ---------------------------------------------------------------------------

def test_github_property_match():
    repo_a = _make_repo("org/a", custom_properties={"service": "checkout"})
    repo_b = _make_repo("org/b", custom_properties={"service": "payments"})
    store = _make_store([repo_a, repo_b], [])
    matcher = CatalogMatcher(store)

    entry = _parse_entity({
        "id": "svc", "type": "service",
        "matchers": {"repos": [{"github_property": {"name": "service", "value": "checkout"}}]},
    })
    memberships = matcher.evaluate(entry, "catalog:service:svc", NOW)
    assert len(memberships) == 1
    assert memberships[0].technical_entity_id == repo_a.id


def test_github_property_missing_raw_no_match():
    """Repos without custom_properties in raw are safely skipped."""
    repo = _make_repo("org/no-props")  # raw has empty custom_properties
    store = _make_store([repo], [])
    matcher = CatalogMatcher(store)

    entry = _parse_entity({
        "id": "svc", "type": "service",
        "matchers": {"repos": [{"github_property": {"name": "service", "value": "anything"}}]},
    })
    memberships = matcher.evaluate(entry, "catalog:service:svc", NOW)
    assert memberships == []


# ---------------------------------------------------------------------------
# Account matchers
# ---------------------------------------------------------------------------

def test_account_id_match():
    acct = _make_account("aws:111111111111")
    store = _make_store([], [acct, _make_account("aws:999999999999")])
    matcher = CatalogMatcher(store)

    entry = _parse_entity({
        "id": "svc", "type": "service",
        "matchers": {"accounts": [{"id": "aws:111111111111"}]},
    })
    memberships = matcher.evaluate(entry, "catalog:service:svc", NOW)
    assert len(memberships) == 1
    assert memberships[0].technical_entity_id == "aws:111111111111"
    assert memberships[0].technical_entity_type == "cloud_account"


def test_account_tags_match():
    acct_a = _make_account("aws:111", tags={"service": "checkout", "env": "prod"})
    acct_b = _make_account("aws:222", tags={"service": "payments"})
    store = _make_store([], [acct_a, acct_b])
    matcher = CatalogMatcher(store)

    entry = _parse_entity({
        "id": "svc", "type": "service",
        "matchers": {"accounts": [{"tags": {"service": "checkout"}}]},
    })
    memberships = matcher.evaluate(entry, "catalog:service:svc", NOW)
    assert len(memberships) == 1
    assert memberships[0].technical_entity_id == "aws:111"


def test_account_environment_match():
    prod = _make_account("aws:111", environment="prod")
    dev = _make_account("aws:222", environment="dev")
    store = _make_store([], [prod, dev])
    matcher = CatalogMatcher(store)

    entry = _parse_entity({
        "id": "svc", "type": "service",
        "matchers": {"accounts": [{"environment": "prod"}]},
    })
    memberships = matcher.evaluate(entry, "catalog:service:svc", NOW)
    assert len(memberships) == 1
    assert memberships[0].technical_entity_id == "aws:111"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_multiple_rules_matching_same_repo_deduplicates():
    """If two rules match the same repo, only one membership is created."""
    repo = _make_repo("my-org/checkout-api", topics=["checkout"])
    store = _make_store([repo], [])
    matcher = CatalogMatcher(store)

    entry = _parse_entity({
        "id": "svc", "type": "service",
        "matchers": {"repos": [
            {"full_name": "my-org/checkout-api"},     # rule 0 — exact
            {"topics": {"any": ["checkout"]}},        # rule 1 — also matches
        ]},
    })
    memberships = matcher.evaluate(entry, "catalog:service:svc", NOW)
    assert len(memberships) == 1
    # First matching rule wins the matched_by label
    assert "repos[0]" in memberships[0].matched_by


# ---------------------------------------------------------------------------
# Mixed repos + accounts
# ---------------------------------------------------------------------------

def test_repos_and_accounts_both_matched():
    repo = _make_repo("my-org/api")
    acct = _make_account("aws:111")
    store = _make_store([repo], [acct])
    matcher = CatalogMatcher(store)

    entry = _parse_entity({
        "id": "svc", "type": "service",
        "matchers": {
            "repos": [{"full_name": "my-org/api"}],
            "accounts": [{"id": "aws:111"}],
        },
    })
    memberships = matcher.evaluate(entry, "catalog:service:svc", NOW)
    assert len(memberships) == 2
    types = {m.technical_entity_type for m in memberships}
    assert types == {"repository", "cloud_account"}


# ---------------------------------------------------------------------------
# Store caching
# ---------------------------------------------------------------------------

def test_store_loaded_once_across_multiple_evaluate_calls():
    """Repos and accounts are loaded once and cached for the syncer lifetime."""
    repo = _make_repo("org/a")
    store = _make_store([repo], [])
    matcher = CatalogMatcher(store)

    entry = _parse_entity({
        "id": "svc1", "type": "service",
        "matchers": {"repos": [{"full_name": "org/a"}]},
    })
    entry2 = _parse_entity({
        "id": "svc2", "type": "service",
        "matchers": {"repos": [{"full_name": "org/a"}]},
    })

    matcher.evaluate(entry, "catalog:service:svc1", NOW)
    matcher.evaluate(entry2, "catalog:service:svc2", NOW)

    # query() should have been called once per type (repos) — not once per entity
    repo_calls = [c for c in store.query.call_args_list if c.args[0] is Repository]
    assert len(repo_calls) == 1
