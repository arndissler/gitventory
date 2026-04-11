"""OwnershipSyncer — assign owning_team_id on repos from GitHub team membership.

Design principles
-----------------
- **Explicit beats inferred.**  If ``owning_team_id`` is already set (from YAML or
  catalog) it is never overwritten unless ``force=True`` is passed.
- **Multi-provider.** Reads ``team.identities`` where ``provider == "github_team"``
  for the primary mapping source.  Falls back to the legacy ``team.github_team_slug``
  field for backwards compatibility.
- **Read-only on GitHub.** Only calls ``list_team_repos`` — no writes to GitHub.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gitventory.adapters.github.adapter import GitHubAdapterConfig
    from gitventory.store.base import AbstractStore

logger = logging.getLogger(__name__)


class OwnershipSyncer:
    """Assign ``owning_team_id`` on repositories based on GitHub team membership."""

    def __init__(self, github_config: "GitHubAdapterConfig", store: "AbstractStore") -> None:
        self._config = github_config
        self._store = store

    def sync(self, force: bool = False) -> dict[str, int]:
        """Assign owning_team_id on repositories.

        Parameters
        ----------
        force:
            If True, overwrite existing ``owning_team_id`` assignments.
            If False (default), repos that already have an owner are skipped.

        Returns
        -------
        dict with keys ``repos_updated`` and ``teams_processed``.
        """
        from gitventory.adapters.github.client import GitHubClient
        from gitventory.models.repository import Repository
        from gitventory.models.team import Team

        # Build {"{org}/{slug}": "team:{party_id}"} mapping
        slug_to_party = self._build_slug_map()
        if not slug_to_party:
            logger.info("Ownership sync: no GitHub team identities found in any team record.")
            return {"repos_updated": 0, "teams_processed": 0}

        client = GitHubClient(
            self._config.auth,
            rate_limit_sleep=self._config.rate_limit_sleep_seconds,
        )
        try:
            repos_updated = 0
            teams_processed = 0

            for org_slug, party_id in slug_to_party.items():
                org, team_slug = org_slug.split("/", 1)
                logger.debug("Ownership sync: fetching repos for team %s/%s", org, team_slug)
                gh_repos = client.list_team_repos(org, team_slug)
                teams_processed += 1

                for gh_repo in gh_repos:
                    stable_id = f"github:{gh_repo.id}"
                    repo = self._store.get(Repository, stable_id)
                    if repo is None:
                        logger.debug(
                            "Ownership sync: repo %s (%s) not in store — skipping",
                            gh_repo.full_name, stable_id,
                        )
                        continue

                    if repo.owning_team_id and not force:
                        logger.debug(
                            "Ownership sync: %s already owned by %s — skipping (use force=True to override)",
                            gh_repo.full_name, repo.owning_team_id,
                        )
                        continue

                    self._store.patch(Repository, stable_id, {"owning_team_id": party_id})
                    logger.debug(
                        "Ownership sync: assigned %s → %s", gh_repo.full_name, party_id
                    )
                    repos_updated += 1

        finally:
            client.close()

        logger.info(
            "Ownership sync complete: %d repos updated across %d teams",
            repos_updated, teams_processed,
        )
        return {"repos_updated": repos_updated, "teams_processed": teams_processed}

    def _build_slug_map(self) -> dict[str, str]:
        """Return {"{org}/{slug}": "team:{party_id}"} from stored team records.

        Sources (in precedence order per team):
        1. ``identities`` where ``provider == "github_team"`` — value is "{org}/{slug}"
        2. Legacy ``github_team_slug`` combined with each org in config
        """
        from gitventory.models.team import Team

        mapping: dict[str, str] = {}
        teams = self._store.query(Team, {})

        for team in teams:
            party_id = team.id  # "team:{slug}"

            # Primary: structured identities
            for identity in team.identities:
                if identity.provider == "github_team":
                    org_slug = identity.value  # expected format: "{org}/{slug}"
                    if "/" in org_slug:
                        mapping[org_slug] = party_id
                        logger.debug(
                            "Ownership map (identity): %s → %s", org_slug, party_id
                        )
                    else:
                        logger.warning(
                            "Team %r has github_team identity %r without org prefix — skipping. "
                            "Expected format: 'my-org/team-slug'",
                            team.id, org_slug,
                        )

            # Legacy fallback: github_team_slug field
            if team.github_team_slug:
                for org in self._config.orgs:
                    org_slug = f"{org}/{team.github_team_slug}"
                    if org_slug not in mapping:
                        mapping[org_slug] = party_id
                        logger.debug(
                            "Ownership map (legacy slug): %s → %s", org_slug, party_id
                        )

        return mapping
