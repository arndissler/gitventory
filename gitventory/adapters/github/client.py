"""Thin wrapper around PyGithub that handles auth, rate limits, and pagination."""

from __future__ import annotations

import logging
import time
from typing import Iterator

from github import Auth, Github, GithubException, GithubIntegration
from github.Repository import Repository as GHRepository

from gitventory.adapters.github.auth import AppAuthConfig, TokenAuthConfig, TokenPerOrgConfig

logger = logging.getLogger(__name__)


class GitHubClient:
    """Wraps PyGithub with multi-mode auth and rate-limit awareness.

    A separate ``Github`` instance is created per organisation so that each
    org uses the correct scoped credential:

    - App auth:          per-org installation token (1 h TTL, auto-refreshed)
    - token_per_org:     per-org PAT
    - token (global):    same ``Github`` instance reused for every org
    """

    def __init__(self, auth_config, rate_limit_sleep: float = 1.0) -> None:
        self._auth = auth_config
        self._rate_limit_sleep = rate_limit_sleep
        self._org_clients: dict[str, Github] = {}

    # ------------------------------------------------------------------
    # Per-org Github instance factory
    # ------------------------------------------------------------------

    def _get_gh(self, org: str) -> Github:
        """Return a cached ``Github`` instance for *org*, creating it if needed."""
        if org not in self._org_clients:
            self._org_clients[org] = self._build_gh(org)
        return self._org_clients[org]

    def _build_gh(self, org: str) -> Github:
        """Construct the right ``Github`` client for the given org and auth mode."""
        auth = self._auth

        if isinstance(auth, AppAuthConfig):
            return self._build_gh_app(org, auth)

        if isinstance(auth, TokenPerOrgConfig):
            token = auth.token_for(org)  # raises KeyError with a clear message if missing
            return Github(auth=Auth.Token(token), per_page=100)

        # TokenAuthConfig — global token, same client for every org
        return Github(auth=Auth.Token(auth.token), per_page=100)

    def _build_gh_app(self, org: str, auth: AppAuthConfig) -> Github:
        """Generate a per-org installation token for a GitHub App."""
        private_key = auth.resolve_private_key()
        app_auth = Auth.AppAuth(auth.app_id, private_key)
        gi = GithubIntegration(auth=app_auth)

        if org in auth.installation_ids:
            # Use the pinned installation ID — skips one API call
            installation = gi.get_installation(auth.installation_ids[org])
            logger.debug(
                "GitHub App: using pinned installation %d for org %r",
                auth.installation_ids[org], org,
            )
        else:
            # Auto-discover the installation for this org
            installation = gi.get_org_installation(org)
            logger.debug(
                "GitHub App: discovered installation %d for org %r",
                installation.id, org,
            )

        # get_github_for_installation() uses Auth.AppInstallationAuth which
        # transparently refreshes the 1-hour token before it expires.
        return installation.get_github_for_installation()

    # ------------------------------------------------------------------
    # API methods (unchanged — they receive GHRepository objects that are
    # already bound to the correct Github instance from _build_gh)
    # ------------------------------------------------------------------

    def list_repos(self, org: str, include_archived: bool = False) -> Iterator[GHRepository]:
        """Yield all repositories in an organisation."""
        gh = self._get_gh(org)
        try:
            organisation = gh.get_organization(org)
        except GithubException as e:
            logger.error("Failed to fetch organisation %r: %s", org, e)
            return

        for repo in organisation.get_repos(type="all"):
            if repo.archived and not include_archived:
                logger.debug("Skipping archived repo: %s", repo.full_name)
                continue
            self._maybe_sleep()
            yield repo

    def get_repo_contents(self, repo: GHRepository, path: str) -> list | None:
        """Return directory listing or None if path doesn't exist."""
        try:
            return repo.get_contents(path)  # type: ignore[return-value]
        except GithubException as e:
            if e.status == 404:
                return None
            raise

    def get_file_content(self, repo: GHRepository, path: str) -> str | None:
        """Return decoded text content of a file, or None if not found."""
        try:
            content_file = repo.get_contents(path)
            if isinstance(content_file, list):
                return None  # It's a directory
            return content_file.decoded_content.decode("utf-8", errors="replace")
        except GithubException as e:
            if e.status == 404:
                return None
            raise

    def get_secret_scanning_alerts(self, repo: GHRepository) -> list:
        """Return all secret scanning alerts for a repo."""
        try:
            return list(repo.get_secret_scanning_alerts())
        except GithubException as e:
            if e.status in (403, 404, 422):
                logger.debug(
                    "Secret scanning not available for %s: %s", repo.full_name, e.data
                )
                return []
            raise

    def get_code_scanning_alerts(self, repo: GHRepository) -> list:
        """Return all code scanning alerts for a repo."""
        try:
            return list(repo.get_codescan_alerts())
        except GithubException as e:
            if e.status in (403, 404, 422):
                logger.debug(
                    "Code scanning not available for %s: %s", repo.full_name, e.data
                )
                return []
            raise

    def get_dependabot_alerts(self, repo: GHRepository) -> list:
        """Return all Dependabot alerts for a repo."""
        try:
            return list(repo.get_dependabot_alerts())
        except GithubException as e:
            if e.status in (403, 404, 422):
                logger.debug(
                    "Dependabot alerts not available for %s: %s", repo.full_name, e.data
                )
                return []
            raise

    def _maybe_sleep(self) -> None:
        if self._rate_limit_sleep > 0:
            time.sleep(self._rate_limit_sleep)

    def close(self) -> None:
        for gh in self._org_clients.values():
            gh.close()
        self._org_clients.clear()
