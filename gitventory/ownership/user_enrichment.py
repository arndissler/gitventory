"""UserEnrichmentSyncer — patch email, Slack handle, and properties onto
discovered User entities from a hand-maintained ``users.yaml`` file.

Why this exists
---------------
GitHub's API does not expose email addresses for most users (they are hidden
by default).  This syncer bridges the gap: operators maintain a ``users.yaml``
file that maps users to contact info, and this syncer patches that data onto
the discovered ``User`` records in the store.

Match key formats (in order of increasing stability)
-----------------------------------------------------
``user: alice``
    Bare login — matched against ``User.login`` for any provider.  Convenient
    but weakest: login names can change and are not provider-scoped.  If the
    same login exists for multiple providers a warning is logged and the first
    match is used.

``user: github:user:alice``
    Provider-scoped login — matched against ``User.provider == "github"`` AND
    ``User.login == "alice"``.  More explicit; safe when multiple providers are
    in use.

``id: github:user:12345678``
    Stable numeric ID — matched against the exact ``User.id`` stored in the
    database.  Immutable: survives login renames.  Use this for long-term
    correctness when the numeric ID is known.

``login: alice``
    **Deprecated alias** for bare ``user: alice``.  Accepted without warnings
    so existing ``users.yaml`` files continue to work.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

from gitventory.adapters.static_yaml.schema import UserEntry, UsersFile

if TYPE_CHECKING:
    from gitventory.models.user import User
    from gitventory.store.base import AbstractStore

logger = logging.getLogger(__name__)


class UserEnrichmentSyncer:
    """Patch contact info from ``users.yaml`` onto discovered User entities."""

    def __init__(self, users_yaml_path: str, store: "AbstractStore") -> None:
        self._path = users_yaml_path
        self._store = store

    def sync(self) -> dict[str, int]:
        """Read ``users.yaml``, resolve each entry to a User, patch contact fields.

        Returns
        -------
        dict with keys ``users_enriched`` and ``unmatched_refs``.
        """
        from gitventory.models.user import User

        entries = self._load_yaml()
        if not entries:
            return {"users_enriched": 0, "unmatched_refs": 0}

        all_users = self._store.query(User, {})
        enriched = 0
        unmatched = 0

        for entry in entries:
            user = self._resolve_entry(entry, all_users)
            ref = entry.id or entry.user  # for logging

            if user is None:
                logger.debug(
                    "User enrichment: %r not found in store — "
                    "not yet collected, or check for typos.",
                    ref,
                )
                unmatched += 1
                continue

            updates: dict = {}
            if entry.email:
                updates["email"] = entry.email
            if entry.slack_handle:
                updates["slack_handle"] = entry.slack_handle
            if entry.properties:
                updates["properties"] = entry.properties

            if updates:
                self._store.patch(User, user.id, updates)
                logger.debug("User enrichment: patched %s (ref=%r)", user.id, ref)
                enriched += 1

        if unmatched:
            logger.warning(
                "User enrichment: %d ref(s) in users.yaml had no matching user in store. "
                "Run a full GitHub collection first, or check for typos.",
                unmatched,
            )

        logger.info(
            "User enrichment complete: %d users enriched, %d unmatched",
            enriched, unmatched,
        )
        return {"users_enriched": enriched, "unmatched_refs": unmatched}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_entry(self, entry: UserEntry, all_users: "list[User]") -> "Optional[User]":
        """Resolve a UserEntry to a User record using the appropriate lookup strategy."""
        if entry.id:
            return self._resolve_by_id(entry.id, all_users)
        # entry.user is guaranteed non-None after the UserEntry validator
        return self._resolve_by_user_ref(entry.user, all_users)  # type: ignore[arg-type]

    def _resolve_by_id(self, stable_id: str, all_users: "list[User]") -> "Optional[User]":
        """Exact stable ID lookup — ``id: github:user:12345678``."""
        result = next((u for u in all_users if u.id == stable_id), None)
        if result is None:
            logger.debug("User enrichment: stable ID %r not in store.", stable_id)
        return result

    def _resolve_by_user_ref(self, ref: str, all_users: "list[User]") -> "Optional[User]":
        """Login-based lookup — bare login or provider-scoped login."""
        if ":" not in ref:
            # Bare login: match any provider
            matches = [u for u in all_users if u.login == ref]
            if len(matches) > 1:
                providers = ", ".join(u.provider for u in matches)
                logger.warning(
                    "User enrichment: login %r matches %d users across providers (%s). "
                    "Using first match. Use 'user: provider:user:login' to be explicit.",
                    ref, len(matches), providers,
                )
            return matches[0] if matches else None

        # Provider-scoped login: "{provider}:user:{login}"
        parts = ref.split(":", 2)
        if len(parts) != 3 or parts[1] != "user":
            logger.warning(
                "User enrichment: unrecognised user ref format %r — "
                "expected 'provider:user:login' (e.g. 'github:user:alice').",
                ref,
            )
            return None

        provider, login = parts[0], parts[2]
        matches = [u for u in all_users if u.provider == provider and u.login == login]
        return matches[0] if matches else None

    def _load_yaml(self) -> list[UserEntry]:
        """Parse users.yaml and return a list of UserEntry objects."""
        p = Path(self._path)
        if not p.exists():
            logger.warning("users.yaml not found, skipping user enrichment: %s", self._path)
            return []

        with p.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        try:
            return UsersFile(**raw).users
        except Exception as exc:
            logger.error("Failed to parse users.yaml: %s", exc)
            return []
