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

# Permissions that qualify a team as a potential owner, in descending priority.
_OWNERSHIP_PERMISSIONS = ("admin", "maintain")


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

    def infer_from_permissions(self, force: bool = False) -> dict[str, int]:
        """Promote a team to owning_team_id based on repo team permission assignments.

        For each repo without an owner (or all repos when ``force=True``), looks up
        ``repo_team_assignments`` with ``maintain`` or ``admin`` permission.  If
        exactly one team holds the highest permission tier it is promoted.  Ambiguous
        cases (multiple teams at the same top tier) are skipped with a warning.

        Parameters
        ----------
        force:
            If True, re-evaluate repos that already have an ``owning_team_id``.
            Existing assignments may be overwritten.

        Returns
        -------
        dict with keys ``repos_promoted``, ``repos_ambiguous``, ``repos_no_signal``.
        """
        from gitventory.models.repo_team_assignment import RepoTeamAssignment
        from gitventory.models.repository import Repository

        if force:
            repos = self._store.query(Repository, {})
        else:
            repos = self._store.query(Repository, {"owning_team_id__isnull": True})

        repos_promoted = 0
        repos_ambiguous = 0
        repos_no_signal = 0

        for repo in repos:
            assignments = self._store.query(RepoTeamAssignment, {"repo_id": repo.id})

            admin_teams = list({a.team_id for a in assignments if a.permission == "admin"})
            maintain_teams = list({a.team_id for a in assignments if a.permission == "maintain"})

            if admin_teams:
                top_teams = admin_teams
                top_perm = "admin"
            elif maintain_teams:
                top_teams = maintain_teams
                top_perm = "maintain"
            else:
                repos_no_signal += 1
                continue

            if len(top_teams) > 1:
                logger.warning(
                    "Infer ownership: %s — ambiguous, %d teams share %s permission, skipping "
                    "(teams: %s)",
                    repo.full_name,
                    len(top_teams),
                    top_perm,
                    ", ".join(sorted(top_teams)),
                )
                repos_ambiguous += 1
                continue

            winner = top_teams[0]
            self._store.patch(Repository, repo.id, {"owning_team_id": winner})
            logger.debug("Infer ownership: %s → %s (%s)", repo.full_name, winner, top_perm)
            repos_promoted += 1

        logger.info(
            "Ownership inference complete: %d promoted, %d ambiguous, %d no signal",
            repos_promoted, repos_ambiguous, repos_no_signal,
        )
        return {
            "repos_promoted": repos_promoted,
            "repos_ambiguous": repos_ambiguous,
            "repos_no_signal": repos_no_signal,
        }

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
