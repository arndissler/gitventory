"""UserEnrichmentSyncer — patch email, Slack handle, and properties onto
discovered User entities from a hand-maintained ``users.yaml`` file.

Why this exists
---------------
GitHub's API does not expose email addresses for most users (they are hidden
by default).  This syncer bridges the gap: operators maintain a ``users.yaml``
file that maps GitHub logins to contact info, and this syncer patches that
data onto the discovered ``User`` records in the store.

Match key: ``login``
The login is used as the match key (not the numeric ID) because it is
human-recognisable.  Logins can change in principle, but this is rare in
practice and easy to fix by updating a single YAML entry.

Operators are informed of unmatched YAML entries (e.g. typos, departed users)
via the ``unmatched_logins`` count in the return value.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from gitventory.adapters.static_yaml.schema import UsersFile

if TYPE_CHECKING:
    from gitventory.store.base import AbstractStore

logger = logging.getLogger(__name__)


class UserEnrichmentSyncer:
    """Patch contact info from ``users.yaml`` onto discovered User entities."""

    def __init__(self, users_yaml_path: str, store: "AbstractStore") -> None:
        self._path = users_yaml_path
        self._store = store

    def sync(self) -> dict[str, int]:
        """Read ``users.yaml``, match by login, patch email/slack_handle/properties.

        Returns
        -------
        dict with keys ``users_enriched`` and ``unmatched_logins``.
        """
        from gitventory.models.user import User

        enrichments = self._load_yaml()
        if not enrichments:
            return {"users_enriched": 0, "unmatched_logins": 0}

        all_users = self._store.query(User, {})
        login_to_user: dict[str, User] = {u.login: u for u in all_users}

        enriched = 0
        unmatched = 0

        for login, entry in enrichments.items():
            user = login_to_user.get(login)
            if user is None:
                logger.debug(
                    "User enrichment: login %r not found in store — not yet collected?",
                    login,
                )
                unmatched += 1
                continue

            updates: dict = {}
            if entry.get("email"):
                updates["email"] = entry["email"]
            if entry.get("slack_handle"):
                updates["slack_handle"] = entry["slack_handle"]
            if entry.get("properties"):
                updates["properties"] = entry["properties"]

            if updates:
                self._store.patch(User, user.id, updates)
                logger.debug("User enrichment: patched %s (%s)", user.id, login)
                enriched += 1

        if unmatched:
            logger.warning(
                "User enrichment: %d login(s) in users.yaml had no matching user in store. "
                "Run a full GitHub collection first, or check for typos.",
                unmatched,
            )

        logger.info(
            "User enrichment complete: %d users enriched, %d unmatched",
            enriched, unmatched,
        )
        return {"users_enriched": enriched, "unmatched_logins": unmatched}

    def _load_yaml(self) -> dict[str, dict]:
        """Return {login: {email, slack_handle, properties}} from users.yaml."""
        p = Path(self._path)
        if not p.exists():
            logger.warning("users.yaml not found, skipping user enrichment: %s", self._path)
            return {}

        with p.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        try:
            file = UsersFile(**raw)
        except Exception as exc:
            logger.error("Failed to parse users.yaml: %s", exc)
            return {}

        return {
            entry.login: {
                "email": entry.email,
                "slack_handle": entry.slack_handle,
                "properties": entry.properties,
            }
            for entry in file.users
            if entry.login
        }
