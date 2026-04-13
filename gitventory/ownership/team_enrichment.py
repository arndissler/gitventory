"""TeamEnrichmentSyncer — copy contact info from YAML-defined teams onto
GitHub-discovered team records.

How it works
------------
The GitHub adapter discovers teams as ``Team`` entities with
``id = "github:team:{numeric_id}"`` and ``source_adapter = "github"``.

The StaticYamlAdapter yields ``Team`` entities with ``id = "team:{slug}"``
and ``source_adapter = "static_yaml"``.  These YAML teams carry the contact
and organisational metadata that GitHub doesn't expose (email, Slack channel,
cost centre, etc.).

This syncer bridges them: for every YAML team that has a ``github_team``
identity entry pointing to ``{org}/{slug}``, it finds the matching discovered
team in the store and patches it with the YAML contact/metadata fields.

The YAML team record itself is left intact so that ``owning_team_id`` links
that point to ``team:{slug}`` remain valid.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gitventory.store.base import AbstractStore

logger = logging.getLogger(__name__)


class TeamEnrichmentSyncer:
    """Copy contact info from YAML teams to their GitHub-discovered counterparts."""

    def __init__(self, store: "AbstractStore") -> None:
        self._store = store

    def sync(self) -> dict[str, int]:
        """Match YAML teams to discovered teams and patch contact fields.

        Returns
        -------
        dict with key ``teams_enriched``.
        """
        from gitventory.models.team import Team

        # Build map: "org/slug" → YAML Team record
        yaml_teams = self._store.query(Team, {"source_adapter": "static_yaml"})
        slug_to_yaml: dict[str, Team] = {}
        for team in yaml_teams:
            for identity in team.identities:
                if identity.provider == "github_team" and "/" in identity.value:
                    slug_to_yaml[identity.value] = team

        if not slug_to_yaml:
            logger.debug("Team enrichment: no YAML teams with github_team identities found.")
            return {"teams_enriched": 0}

        # Find discovered GitHub teams and match them
        gh_teams = self._store.query(Team, {"source_adapter": "github"})
        enriched = 0

        for gh_team in gh_teams:
            if not gh_team.github_org or not gh_team.github_team_slug:
                continue
            key = f"{gh_team.github_org}/{gh_team.github_team_slug}"
            yaml_team = slug_to_yaml.get(key)
            if yaml_team is None:
                continue

            updates: dict = {}
            if yaml_team.email:
                updates["email"] = yaml_team.email
            if yaml_team.slack_channel:
                updates["slack_channel"] = yaml_team.slack_channel
            if yaml_team.contacts:
                updates["contacts"] = yaml_team.contacts
            if yaml_team.properties:
                updates["properties"] = yaml_team.properties

            if updates:
                self._store.patch(Team, gh_team.id, updates)
                logger.debug(
                    "Team enrichment: patched %s from YAML team %s",
                    gh_team.id, yaml_team.id,
                )
                enriched += 1

        logger.info("Team enrichment complete: %d teams enriched", enriched)
        return {"teams_enriched": enriched}
