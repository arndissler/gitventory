"""GitHubAdapter — collects repositories, GHAS alerts, and OIDC deployment mappings."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Iterator, List, Optional

from pydantic import model_validator

from gitventory.adapters.base import AbstractAdapter, AdapterConfig
from gitventory.adapters.github.auth import (
    AppAuthConfig,
    GitHubAuth,
    TokenAuthConfig,
    TokenPerOrgConfig,
)
from gitventory.adapters.github.client import GitHubClient
from gitventory.adapters.github.mappers import (
    code_scanning_alert_to_entity,
    dependabot_alert_to_entity,
    gh_team_to_entity,
    gh_user_to_entity,
    repo_collaborator_to_entity,
    repo_team_assignment_to_entity,
    repo_to_entity,
    secret_alert_to_entity,
    team_member_to_entity,
)
from gitventory.adapters.github.workflow_parser import parse_workflows
from gitventory.models.base import InventoryEntity
from gitventory.registry import register_adapter

logger = logging.getLogger(__name__)


class GitHubAdapterConfig(AdapterConfig):
    """Configuration for the GitHub adapter.

    Auth modes (set ``auth.type``):
      app           GitHub App — recommended for enterprise, one App covers all orgs.
      token_per_org One PAT per organisation.
      token         Single global PAT (simple / local / single-org use only).

    Backwards compatibility: if a top-level ``token`` key is present (old format),
    it is automatically migrated to ``auth: {type: token, token: ...}``.
    """

    auth: GitHubAuth = TokenAuthConfig()

    orgs: List[str] = []
    include_archived: bool = False
    collect_ghas_alerts: bool = True
    collect_secret_scanning: bool = True
    collect_dependabot: bool = True
    parse_workflows: bool = True
    # Team and collaborator discovery
    collect_github_teams: bool = True
    """Discover GitHub teams from the API and collect repo→team assignments."""
    collect_team_members: bool = True
    """Collect team members with their roles (requires collect_github_teams=True)."""
    collect_collaborators: bool = False
    """Collect direct and outside collaborators per repo (opt-in — adds ~2 API calls/repo)."""
    collaborator_affiliation: str = "all"
    """Collaborator affiliation filter: ``all``, ``direct``, or ``outside``."""
    rate_limit_min_remaining: int = 100
    """Pause collection when fewer than this many GitHub API requests remain."""
    rate_limit_sleep_seconds: float = 1.0
    smart_rate_limiting: bool = True
    """When True, skip the per-call sleep and rely on check_rate_limit() to pause
    only when quota is actually low.  Set to False to restore the unconditional
    rate_limit_sleep_seconds delay between every API call."""
    per_page: int = 100
    max_entity_errors: int = 10
    """How many per-entity validation errors to tolerate before aborting the run.
    0 = strict (fail on first error).  -1 = never hard-fail, always warn and skip."""
    http_timeout: int = 60
    """Read timeout in seconds for GitHub API requests.
    PyGithub default is 15s — increase for large orgs with many paginated results."""

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_token(cls, data: object) -> object:
        """Migrate old-style ``token: "..."`` top-level key to the auth sub-object.

        This allows existing config.yaml files to keep working without changes.
        """
        if not isinstance(data, dict):
            return data
        if "token" in data and "auth" not in data:
            token = data.pop("token") or os.environ.get("GITHUB_TOKEN", "")
            data["auth"] = {"type": "token", "token": token}
        return data


@register_adapter
class GitHubAdapter(AbstractAdapter):
    """Collects GitHub repositories, GHAS alerts, and OIDC deployment mappings."""

    ADAPTER_NAME = "github"
    CONFIG_CLASS = GitHubAdapterConfig

    def __init__(self, config: GitHubAdapterConfig) -> None:
        super().__init__(config)
        self._collected_at = datetime.now(timezone.utc)
        self._client: Optional[GitHubClient] = None
        self._collected_orgs: dict[str, datetime] = {}
        """Maps org name → timestamp of when _collect_org started for that org."""

    def validate_connectivity(self) -> bool:
        cfg: GitHubAdapterConfig = self.config  # type: ignore[assignment]
        auth = cfg.auth

        if isinstance(auth, AppAuthConfig):
            if not auth.app_id:
                logger.error("GitHub App auth: app_id is not set.")
                return False
            if not auth.private_key and not auth.private_key_file:
                logger.error(
                    "GitHub App auth: neither private_key nor private_key_file is set."
                )
                return False
            logger.info(
                "GitHub adapter: App auth configured (app_id=%d, %d org(s))",
                auth.app_id, len(cfg.orgs),
            )

        elif isinstance(auth, TokenPerOrgConfig):
            missing = [org for org in cfg.orgs if not auth.org_tokens.get(org)]
            if missing:
                logger.error(
                    "GitHub token_per_org auth: no token configured for org(s): %s. "
                    "Add them under adapters.github.auth.org_tokens in config.yaml.",
                    ", ".join(missing),
                )
                return False
            logger.info(
                "GitHub adapter: per-org token auth configured (%d org(s))",
                len(cfg.orgs),
            )

        else:  # TokenAuthConfig
            if not auth.token:
                logger.error(
                    "GitHub adapter: no token configured. "
                    "Set GITHUB_TOKEN or adapters.github.auth.token in config.yaml."
                )
                return False
            logger.info("GitHub adapter: global token auth configured (%d org(s))", len(cfg.orgs))

        if not cfg.orgs:
            logger.warning("GitHub adapter: no organisations configured — nothing to collect.")

        return True

    def collect(self) -> Iterator[InventoryEntity]:
        cfg: GitHubAdapterConfig = self.config  # type: ignore[assignment]
        self._collected_at = datetime.now(timezone.utc)
        self._collected_orgs = {}
        self._client = GitHubClient(
            auth_config=cfg.auth,
            rate_limit_sleep=cfg.rate_limit_sleep_seconds,
            http_timeout=cfg.http_timeout,
            smart_rate_limiting=cfg.smart_rate_limiting,
        )
        try:
            for org in cfg.orgs:
                logger.info("GitHub adapter: scanning org %r", org)
                yield from self._collect_org(org)
        finally:
            self._client.close()

    def collect_one(self, full_name: str) -> Iterator[InventoryEntity]:
        """Collect a single repository by its ``org/name`` full name.

        Only repo-level entities are collected (RepoTeamAssignment, RepoCollaborator).
        Org-level team and member collection is not performed — those entities are
        only updated during a full org scan.
        """
        cfg: GitHubAdapterConfig = self.config  # type: ignore[assignment]
        self._collected_at = datetime.now(timezone.utc)
        self._client = GitHubClient(
            auth_config=cfg.auth,
            rate_limit_sleep=cfg.rate_limit_sleep_seconds,
            http_timeout=cfg.http_timeout,
            smart_rate_limiting=cfg.smart_rate_limiting,
        )
        try:
            logger.info("GitHub adapter: collecting single repo %r", full_name)
            gh_repo = self._client.get_repo(full_name)
            yield from self._collect_repo(gh_repo)
        finally:
            self._client.close()

    def get_collected_orgs(self) -> dict[str, datetime]:
        """Return the orgs processed in the last ``collect()`` call with start times.

        Used by the runner to scope stale-row cleanup after a full collection.
        """
        return dict(self._collected_orgs)

    # ------------------------------------------------------------------
    # Per-organisation / per-repository collection
    # ------------------------------------------------------------------

    def _collect_org(self, org: str) -> Iterator[InventoryEntity]:
        cfg: GitHubAdapterConfig = self.config  # type: ignore[assignment]
        self._collected_orgs[org] = self._collected_at

        # --- Phase A: Org-level team and member discovery ---
        if cfg.collect_github_teams:
            seen_users: set[str] = set()
            logger.info("GitHub adapter: collecting teams for org %r", org)
            for gh_team in self._client.list_org_teams(org):
                yield gh_team_to_entity(gh_team, org, self._collected_at)

                if cfg.collect_team_members:
                    team_id = f"github:team:{gh_team.id}"
                    for gh_user, role in self._client.get_team_members(org, gh_team.slug):
                        user_id = f"github:user:{gh_user.id}"
                        if user_id not in seen_users:
                            seen_users.add(user_id)
                            yield gh_user_to_entity(gh_user, self._collected_at)
                        yield team_member_to_entity(team_id, gh_user, role, org, self._collected_at)

        # --- Phase B: Per-repo collection ---
        for gh_repo in self._client.list_repos(org, include_archived=cfg.include_archived):
            yield from self._collect_repo(gh_repo)

    def _collect_repo(self, gh_repo) -> Iterator[InventoryEntity]:  # type: ignore[no-untyped-def]
        cfg: GitHubAdapterConfig = self.config  # type: ignore[assignment]
        repo_id = f"github:{gh_repo.id}"
        logger.debug("Processing repo: %s", gh_repo.full_name)

        secret_alerts = []
        code_alerts = []
        dependabot_alerts = []

        if cfg.collect_ghas_alerts:
            if cfg.collect_secret_scanning:
                secret_alerts = self._client.get_secret_scanning_alerts(gh_repo)
            if cfg.collect_ghas_alerts:
                code_alerts = self._client.get_code_scanning_alerts(gh_repo)
            if cfg.collect_dependabot:
                dependabot_alerts = self._client.get_dependabot_alerts(gh_repo)

        open_secret = sum(1 for a in secret_alerts if a.state == "open")
        open_code = sum(1 for a in code_alerts if getattr(a, "state", None) == "open")
        open_dependabot = sum(1 for a in dependabot_alerts if getattr(a, "state", None) == "open")

        yield repo_to_entity(
            gh_repo,
            collected_at=self._collected_at,
            open_secret_alerts=open_secret,
            open_code_scanning_alerts=open_code,
            open_dependabot_alerts=open_dependabot,
        )

        for alert in secret_alerts:
            yield secret_alert_to_entity(alert, repo_id, self._collected_at)
        for alert in code_alerts:
            yield code_scanning_alert_to_entity(alert, repo_id, self._collected_at)
        for alert in dependabot_alerts:
            yield dependabot_alert_to_entity(alert, repo_id, self._collected_at)

        if cfg.parse_workflows:
            yield from parse_workflows(
                gh_repo, repo_id, self._collected_at, self._client
            )

        org = gh_repo.owner.login

        # --- Repo team assignments ---
        if cfg.collect_github_teams:
            self._client.check_rate_limit(org, cfg.rate_limit_min_remaining)
            for gh_team, permission in self._client.list_repo_teams(gh_repo):
                yield repo_team_assignment_to_entity(
                    repo_id, gh_team, org, permission, self._collected_at
                )

        # --- Direct / outside collaborators (opt-in) ---
        if cfg.collect_collaborators:
            self._client.check_rate_limit(org, cfg.rate_limit_min_remaining)
            seen_collab_users: set[str] = set()
            for gh_user, permission in self._client.list_repo_collaborators(
                gh_repo, cfg.collaborator_affiliation
            ):
                user_id = f"github:user:{gh_user.id}"
                if user_id not in seen_collab_users:
                    seen_collab_users.add(user_id)
                    yield gh_user_to_entity(gh_user, self._collected_at)
                yield repo_collaborator_to_entity(
                    repo_id, gh_user, permission, cfg.collaborator_affiliation,
                    self._collected_at,
                )
