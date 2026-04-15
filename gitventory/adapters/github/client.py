"""Thin wrapper around PyGithub that handles auth, rate limits, and pagination."""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

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
            # Auto-discover the installation — try org first, fall back to
            # personal account (user installation) if the name is not an org.
            try:
                installation = gi.get_org_installation(org)
                logger.debug(
                    "GitHub App: discovered org installation %d for %r",
                    installation.id, org,
                )
            except GithubException as e:
                if e.status != 404:
                    raise
                installation = gi.get_user_installation(org)
                logger.debug(
                    "GitHub App: discovered user installation %d for %r",
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
        """Yield all repositories for an organisation or personal account."""
        gh = self._get_gh(org)
        try:
            owner = gh.get_organization(org)
            logger.debug("Scanning GitHub organisation: %s", org)
        except GithubException as e:
            if e.status != 404:
                logger.error("Failed to fetch organisation %r: %s", org, e)
                return
            # Not an organisation — treat as a personal account
            owner = gh.get_user(org)
            logger.debug("Scanning GitHub personal account: %s", org)

        for repo in owner.get_repos(type="all"):
            if repo.archived and not include_archived:
                logger.debug("Skipping archived repo: %s", repo.full_name)
                continue
            self._maybe_sleep()
            yield repo

    def get_repo(self, full_name: str) -> GHRepository:
        """Fetch a single repository by its ``org/name`` full name."""
        org = full_name.split("/")[0]
        gh = self._get_gh(org)
        return gh.get_repo(full_name)

    def list_team_repos(self, org: str, team_slug: str) -> list[GHRepository]:
        """Return all repositories accessible to a GitHub team.

        Requires the GitHub App / PAT to have Contents or Metadata read permission
        on the target org.  Returns an empty list if the team or org is not found.
        """
        gh = self._get_gh(org)
        try:
            team = gh.get_organization(org).get_team_by_slug(team_slug)
            return list(team.get_repos())
        except GithubException as e:
            if e.status in (403, 404):
                logger.warning(
                    "Cannot list repos for team %r in org %r (HTTP %d): %s",
                    team_slug, org, e.status, e.data,
                )
                return []
            raise

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

    # ------------------------------------------------------------------
    # Team and collaborator discovery
    # ------------------------------------------------------------------

    def list_org_teams(self, org: str) -> Iterator[Any]:
        """Yield all teams in a GitHub organisation."""
        gh = self._get_gh(org)
        try:
            org_obj = gh.get_organization(org)
            for team in org_obj.get_teams():
                self._maybe_sleep()
                yield team
        except GithubException as e:
            if e.status in (403, 404):
                logger.warning(
                    "Cannot list teams for org %r (HTTP %d): %s", org, e.status, e.data
                )
                return
            raise

    def get_team_members(self, org: str, team_slug: str) -> list[tuple[Any, str]]:
        """Return ``(user, role)`` pairs for every member of a team.

        Fetches maintainers and members separately (the API does not return role
        in a single call).  Falls back to an empty list on permission errors.
        """
        gh = self._get_gh(org)
        try:
            team = gh.get_organization(org).get_team_by_slug(team_slug)
            result: list[tuple[Any, str]] = []
            for user in team.get_members(role="maintainer"):
                result.append((user, "maintainer"))
            for user in team.get_members(role="member"):
                result.append((user, "member"))
            return result
        except GithubException as e:
            if e.status in (403, 404):
                logger.warning(
                    "Cannot list members for team %r in org %r (HTTP %d): %s",
                    team_slug, org, e.status, e.data,
                )
                return []
            raise

    def list_repo_teams(self, repo: GHRepository) -> list[tuple[Any, str]]:
        """Return ``(team, permission)`` pairs for every team assigned to a repo.

        The ``repo.get_teams()`` API does not embed the permission level in the
        returned objects for all PyGithub versions, so we read it from the team's
        ``permission`` attribute (set by the API when returned in repo context).
        """
        try:
            result: list[tuple[Any, str]] = []
            for team in repo.get_teams():
                permission = getattr(team, "permission", "pull") or "pull"
                result.append((team, permission))
            return result
        except GithubException as e:
            if e.status in (403, 404):
                logger.debug(
                    "Cannot list teams for repo %s (HTTP %d): %s",
                    repo.full_name, e.status, e.data,
                )
                return []
            raise

    def list_repo_collaborators(
        self, repo: GHRepository, affiliation: str = "all"
    ) -> list[tuple[Any, str]]:
        """Return ``(user, permission)`` pairs for collaborators on a repo.

        ``affiliation`` mirrors the GitHub API parameter: ``direct``, ``outside``,
        or ``all``.  Permission is fetched per-user via a separate API call because
        ``get_collaborators()`` does not return the permission level directly.
        Falls back to an empty list on permission errors.
        """
        try:
            result: list[tuple[Any, str]] = []
            for user in repo.get_collaborators(affiliation=affiliation):
                try:
                    perm = repo.get_collaborator_permission(user.login)
                except GithubException:
                    perm = "pull"
                result.append((user, perm))
                self._maybe_sleep()
            return result
        except GithubException as e:
            if e.status in (403, 404):
                logger.debug(
                    "Cannot list collaborators for repo %s (HTTP %d): %s",
                    repo.full_name, e.status, e.data,
                )
                return []
            raise

    def check_rate_limit(self, org: str, min_remaining: int) -> None:
        """If fewer than *min_remaining* core API requests remain, sleep until reset.

        Uses the ``GET /rate_limit`` endpoint which does not consume quota itself.
        """
        gh = self._get_gh(org)
        try:
            remaining, _ = gh.rate_limiting
            if remaining < min_remaining:
                reset_ts = gh.rate_limiting_resettime
                sleep_for = max(0.0, reset_ts - time.time()) + 1.0
                logger.info(
                    "Rate limit low (%d remaining).  Sleeping %.0fs until reset.",
                    remaining, sleep_for,
                )
                time.sleep(sleep_for)
        except GithubException as e:
            logger.debug("Could not check rate limit: %s", e)

    def _maybe_sleep(self) -> None:
        if self._rate_limit_sleep > 0:
            time.sleep(self._rate_limit_sleep)

    def close(self) -> None:
        for gh in self._org_clients.values():
            gh.close()
        self._org_clients.clear()
