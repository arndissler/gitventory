"""Thin wrapper around PyGithub that handles auth, rate limits, and pagination."""

from __future__ import annotations

import logging
import time
from typing import Iterator

from github import Auth, Github, GithubException
from github.Repository import Repository as GHRepository

logger = logging.getLogger(__name__)


class GitHubClient:
    """Wraps PyGithub with rate-limit awareness."""

    def __init__(self, token: str, rate_limit_sleep: float = 1.0) -> None:
        auth = Auth.Token(token)
        self._gh = Github(auth=auth, per_page=100)
        self._rate_limit_sleep = rate_limit_sleep

    def list_repos(self, org: str, include_archived: bool = False) -> Iterator[GHRepository]:
        """Yield all repositories in an organisation."""
        try:
            organisation = self._gh.get_organization(org)
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
                # 403: GHAS not enabled or no permission
                # 404: repo not found
                # 422: GHAS not available on this plan
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
        self._gh.close()
