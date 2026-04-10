"""Unit tests for GitHubClient — org vs personal account resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest
from github import GithubException

from gitventory.adapters.github.auth import TokenAuthConfig
from gitventory.adapters.github.client import GitHubClient


def _make_client(token: str = "fake-token") -> GitHubClient:
    return GitHubClient(auth_config=TokenAuthConfig(token=token), rate_limit_sleep=0)


def _make_repo(name: str, archived: bool = False) -> MagicMock:
    repo = MagicMock()
    repo.full_name = f"owner/{name}"
    repo.archived = archived
    return repo


# ---------------------------------------------------------------------------
# list_repos — org vs personal account fallback
# ---------------------------------------------------------------------------

@patch("gitventory.adapters.github.client.Github")
def test_list_repos_uses_org_when_found(MockGithub):
    fake_repo = _make_repo("my-repo")
    mock_gh = MockGithub.return_value
    mock_org = MagicMock()
    mock_org.get_repos.return_value = [fake_repo]
    mock_gh.get_organization.return_value = mock_org

    client = _make_client()
    repos = list(client.list_repos("my-org"))

    mock_gh.get_organization.assert_called_once_with("my-org")
    mock_gh.get_user.assert_not_called()
    assert len(repos) == 1


@patch("gitventory.adapters.github.client.Github")
def test_list_repos_falls_back_to_user_on_404(MockGithub):
    fake_repo = _make_repo("personal-repo")
    mock_gh = MockGithub.return_value

    # get_organization raises 404 — this is a personal account
    not_found = GithubException(404, {"message": "Not Found"}, None)
    mock_gh.get_organization.side_effect = not_found

    mock_user = MagicMock()
    mock_user.get_repos.return_value = [fake_repo]
    mock_gh.get_user.return_value = mock_user

    client = _make_client()
    repos = list(client.list_repos("arndissler"))

    mock_gh.get_organization.assert_called_once_with("arndissler")
    mock_gh.get_user.assert_called_once_with("arndissler")
    assert len(repos) == 1


@patch("gitventory.adapters.github.client.Github")
def test_list_repos_propagates_non_404_org_errors(MockGithub):
    mock_gh = MockGithub.return_value
    server_error = GithubException(500, {"message": "Server Error"}, None)
    mock_gh.get_organization.side_effect = server_error

    client = _make_client()
    # Should return empty (logged as error), not raise
    repos = list(client.list_repos("my-org"))
    assert repos == []


@patch("gitventory.adapters.github.client.Github")
def test_list_repos_skips_archived_by_default(MockGithub):
    active = _make_repo("active", archived=False)
    archived = _make_repo("archived", archived=True)
    mock_gh = MockGithub.return_value
    mock_org = MagicMock()
    mock_org.get_repos.return_value = [active, archived]
    mock_gh.get_organization.return_value = mock_org

    client = _make_client()
    repos = list(client.list_repos("my-org", include_archived=False))
    assert len(repos) == 1
    assert repos[0].full_name == "owner/active"


@patch("gitventory.adapters.github.client.Github")
def test_list_repos_includes_archived_when_requested(MockGithub):
    active = _make_repo("active", archived=False)
    archived = _make_repo("archived", archived=True)
    mock_gh = MockGithub.return_value
    mock_org = MagicMock()
    mock_org.get_repos.return_value = [active, archived]
    mock_gh.get_organization.return_value = mock_org

    client = _make_client()
    repos = list(client.list_repos("my-org", include_archived=True))
    assert len(repos) == 2


# ---------------------------------------------------------------------------
# get_repo — single repository fetch
# ---------------------------------------------------------------------------

@patch("gitventory.adapters.github.client.Github")
def test_get_repo_uses_org_for_auth(MockGithub):
    fake_repo = _make_repo("my-repo")
    mock_gh = MockGithub.return_value
    mock_gh.get_repo.return_value = fake_repo

    client = _make_client()
    result = client.get_repo("my-org/my-repo")

    mock_gh.get_repo.assert_called_once_with("my-org/my-repo")
    assert result is fake_repo


@patch("gitventory.adapters.github.client.Github")
def test_get_repo_extracts_org_from_full_name(MockGithub):
    """The org segment of full_name drives which Github instance is used."""
    fake_repo = _make_repo("tool")
    mock_gh = MockGithub.return_value
    mock_gh.get_repo.return_value = fake_repo

    client = _make_client()
    client.get_repo("acme-corp/tool")

    # Only one Github instance should have been created (for "acme-corp")
    assert len(client._org_clients) == 1
    assert "acme-corp" in client._org_clients
