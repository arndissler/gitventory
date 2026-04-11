"""Unit tests for OwnershipSyncer — no network, no real DB."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from gitventory.models.repository import Repository
from gitventory.models.team import ExternalIdentity, Team
from gitventory.ownership.sync import OwnershipSyncer


_NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _make_repo(id: str, full_name: str, owning_team_id=None) -> Repository:
    return Repository(
        id=id,
        provider_id=id.split(":")[-1],
        provider="github",
        source_adapter="github",
        collected_at=_NOW,
        org=full_name.split("/")[0],
        name=full_name.split("/")[1],
        full_name=full_name,
        url=f"https://github.com/{full_name}",
        owning_team_id=owning_team_id,
    )


def _make_team(slug: str, github_team_slug=None, identities=None) -> Team:
    return Team(
        id=f"team:{slug}",
        provider_id=slug,
        source_adapter="static_yaml",
        collected_at=_NOW,
        display_name=slug.replace("-", " ").title(),
        github_team_slug=github_team_slug,
        identities=identities or [],
    )


def _make_config(orgs: list[str]):
    cfg = MagicMock()
    cfg.orgs = orgs
    cfg.rate_limit_sleep_seconds = 0.0
    return cfg


# ---------------------------------------------------------------------------
# _build_slug_map
# ---------------------------------------------------------------------------

def test_build_slug_map_from_identities():
    store = MagicMock()
    store.query.return_value = [
        _make_team(
            "platform-engineering",
            identities=[
                ExternalIdentity(provider="github_team", value="my-org/platform-engineering")
            ],
        )
    ]
    config = _make_config(["my-org"])
    syncer = OwnershipSyncer(config, store)
    slug_map = syncer._build_slug_map()
    assert slug_map == {"my-org/platform-engineering": "team:platform-engineering"}


def test_build_slug_map_from_legacy_slug():
    store = MagicMock()
    store.query.return_value = [
        _make_team("platform-engineering", github_team_slug="platform-engineering")
    ]
    config = _make_config(["my-org"])
    syncer = OwnershipSyncer(config, store)
    slug_map = syncer._build_slug_map()
    assert slug_map == {"my-org/platform-engineering": "team:platform-engineering"}


def test_build_slug_map_multi_org_legacy():
    """Legacy slug expands to one entry per org."""
    store = MagicMock()
    store.query.return_value = [
        _make_team("platform-engineering", github_team_slug="platform-engineering")
    ]
    config = _make_config(["org-a", "org-b"])
    syncer = OwnershipSyncer(config, store)
    slug_map = syncer._build_slug_map()
    assert "org-a/platform-engineering" in slug_map
    assert "org-b/platform-engineering" in slug_map


def test_build_slug_map_identity_takes_precedence_over_legacy():
    """Explicit identity entry should not be overwritten by the legacy fallback."""
    store = MagicMock()
    store.query.return_value = [
        _make_team(
            "platform-engineering",
            github_team_slug="platform-engineering",
            identities=[
                ExternalIdentity(provider="github_team", value="my-org/platform-engineering")
            ],
        )
    ]
    config = _make_config(["my-org"])
    syncer = OwnershipSyncer(config, store)
    slug_map = syncer._build_slug_map()
    # The identity entry is present
    assert slug_map.get("my-org/platform-engineering") == "team:platform-engineering"
    # Only one entry for that org/slug combo
    assert sum(1 for k in slug_map if k == "my-org/platform-engineering") == 1


def test_build_slug_map_missing_org_prefix_warns(caplog):
    """Identity value without org prefix should be skipped with a warning."""
    import logging
    store = MagicMock()
    store.query.return_value = [
        _make_team(
            "platform-engineering",
            identities=[
                ExternalIdentity(provider="github_team", value="no-slash-here")
            ],
        )
    ]
    config = _make_config([])
    syncer = OwnershipSyncer(config, store)
    with caplog.at_level(logging.WARNING, logger="gitventory.ownership.sync"):
        slug_map = syncer._build_slug_map()
    assert slug_map == {}
    assert "no-slash-here" in caplog.text


def test_build_slug_map_non_github_identity_ignored():
    """Only provider==github_team entries contribute to the slug map."""
    store = MagicMock()
    store.query.return_value = [
        _make_team(
            "platform-engineering",
            identities=[
                ExternalIdentity(provider="entraid_group", value="aaaa-bbbb"),
            ],
        )
    ]
    config = _make_config([])
    syncer = OwnershipSyncer(config, store)
    slug_map = syncer._build_slug_map()
    assert slug_map == {}


# ---------------------------------------------------------------------------
# sync() — explicit beats inferred
# ---------------------------------------------------------------------------

def test_sync_assigns_owner_to_unowned_repo():
    store = MagicMock()
    repo = _make_repo("github:111", "my-org/repo-a")  # no owning_team_id
    store.query.return_value = [
        _make_team("platform-engineering", github_team_slug="platform-engineering")
    ]
    store.get.return_value = repo

    config = _make_config(["my-org"])
    gh_repo = MagicMock()
    gh_repo.id = 111
    gh_repo.full_name = "my-org/repo-a"

    with patch("gitventory.adapters.github.client.GitHubClient") as MockClient:
        MockClient.return_value.__enter__ = lambda s: s
        MockClient.return_value.__exit__ = MagicMock(return_value=False)
        MockClient.return_value.list_team_repos.return_value = [gh_repo]
        MockClient.return_value.close = MagicMock()

        syncer = OwnershipSyncer(config, store)
        counts = syncer.sync(force=False)

    store.patch.assert_called_once_with(
        Repository, "github:111", {"owning_team_id": "team:platform-engineering"}
    )
    assert counts["repos_updated"] == 1
    assert counts["teams_processed"] == 1


def test_sync_skips_already_owned_repo_without_force():
    store = MagicMock()
    repo = _make_repo("github:222", "my-org/repo-b")
    repo.owning_team_id = "team:other-team"  # already owned
    store.query.return_value = [
        _make_team("platform-engineering", github_team_slug="platform-engineering")
    ]
    store.get.return_value = repo

    config = _make_config(["my-org"])
    gh_repo = MagicMock()
    gh_repo.id = 222
    gh_repo.full_name = "my-org/repo-b"

    with patch("gitventory.adapters.github.client.GitHubClient") as MockClient:
        MockClient.return_value.list_team_repos.return_value = [gh_repo]
        MockClient.return_value.close = MagicMock()

        syncer = OwnershipSyncer(config, store)
        counts = syncer.sync(force=False)

    store.patch.assert_not_called()
    assert counts["repos_updated"] == 0


def test_sync_overwrites_with_force():
    store = MagicMock()
    repo = _make_repo("github:333", "my-org/repo-c")
    repo.owning_team_id = "team:other-team"
    store.query.return_value = [
        _make_team("platform-engineering", github_team_slug="platform-engineering")
    ]
    store.get.return_value = repo

    config = _make_config(["my-org"])
    gh_repo = MagicMock()
    gh_repo.id = 333
    gh_repo.full_name = "my-org/repo-c"

    with patch("gitventory.adapters.github.client.GitHubClient") as MockClient:
        MockClient.return_value.list_team_repos.return_value = [gh_repo]
        MockClient.return_value.close = MagicMock()

        syncer = OwnershipSyncer(config, store)
        counts = syncer.sync(force=True)

    store.patch.assert_called_once_with(
        Repository, "github:333", {"owning_team_id": "team:platform-engineering"}
    )
    assert counts["repos_updated"] == 1


def test_sync_skips_repo_not_in_store():
    store = MagicMock()
    store.query.return_value = [
        _make_team("platform-engineering", github_team_slug="platform-engineering")
    ]
    store.get.return_value = None  # repo not in store

    config = _make_config(["my-org"])
    gh_repo = MagicMock()
    gh_repo.id = 999
    gh_repo.full_name = "my-org/unknown-repo"

    with patch("gitventory.adapters.github.client.GitHubClient") as MockClient:
        MockClient.return_value.list_team_repos.return_value = [gh_repo]
        MockClient.return_value.close = MagicMock()

        syncer = OwnershipSyncer(config, store)
        counts = syncer.sync(force=False)

    store.patch.assert_not_called()
    assert counts["repos_updated"] == 0


def test_sync_no_teams_returns_early():
    store = MagicMock()
    store.query.return_value = []  # no teams

    config = _make_config(["my-org"])
    syncer = OwnershipSyncer(config, store)
    counts = syncer.sync()

    assert counts == {"repos_updated": 0, "teams_processed": 0}
