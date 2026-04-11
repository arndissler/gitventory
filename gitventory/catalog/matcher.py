"""Catalog matcher — evaluates declarative rules against the store inventory."""

from __future__ import annotations

import fnmatch
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from gitventory.catalog.schema import (
    AccountFieldMatcher,
    AccountIdMatcher,
    AccountTagsMatcher,
    CatalogEntityEntry,
    FullNameMatcher,
    GithubPropertyMatcher,
    TopicsMatcher,
)
from gitventory.models.catalog import CatalogMembership
from gitventory.models.cloud_account import CloudAccount
from gitventory.models.repository import Repository

if TYPE_CHECKING:
    from gitventory.store.base import AbstractStore

logger = logging.getLogger(__name__)


class CatalogMatcher:
    """Evaluates catalog YAML matchers against the live store inventory.

    Repositories and cloud accounts are loaded once on first use and cached
    for the lifetime of this object (one catalog sync run).

    Security
    --------
    Only ``fnmatch`` glob patterns are used — no ``re`` module — preventing
    ReDoS attacks from pathological patterns in the catalog YAML.
    """

    def __init__(self, store: "AbstractStore") -> None:
        self._store = store
        self._repos: list[Repository] | None = None
        self._accounts: list[CloudAccount] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        entity_entry: CatalogEntityEntry,
        catalog_entity_id: str,
        collected_at: datetime,
    ) -> list[CatalogMembership]:
        """Return all CatalogMembership records for one catalog entity entry."""
        repos = self._get_repos()
        accounts = self._get_accounts()

        memberships: dict[str, CatalogMembership] = {}

        # --- repo matchers ---
        for idx, rule in enumerate(entity_entry.matchers.repos):
            matched = self._match_repos(rule, repos)
            for repo in matched:
                if repo.id not in memberships:
                    memberships[repo.id] = CatalogMembership(
                        id=f"membership:{catalog_entity_id}::{repo.id}",
                        provider_id=f"{catalog_entity_id}::{repo.id}",
                        source_adapter="catalog_yaml",
                        collected_at=collected_at,
                        catalog_entity_id=catalog_entity_id,
                        technical_entity_id=repo.id,
                        technical_entity_type="repository",
                        matched_by=self._describe_repo_rule(rule, idx),
                    )

        # --- account matchers ---
        for idx, rule in enumerate(entity_entry.matchers.accounts):
            matched_accts = self._match_accounts(rule, accounts)
            for acct in matched_accts:
                if acct.id not in memberships:
                    memberships[acct.id] = CatalogMembership(
                        id=f"membership:{catalog_entity_id}::{acct.id}",
                        provider_id=f"{catalog_entity_id}::{acct.id}",
                        source_adapter="catalog_yaml",
                        collected_at=collected_at,
                        catalog_entity_id=catalog_entity_id,
                        technical_entity_id=acct.id,
                        technical_entity_type="cloud_account",
                        matched_by=self._describe_account_rule(rule, idx),
                    )

        count = len(memberships)
        if count:
            logger.debug(
                "Catalog matcher: %s → %d match(es)", catalog_entity_id, count
            )
        else:
            logger.debug("Catalog matcher: %s → no matches", catalog_entity_id)

        return list(memberships.values())

    # ------------------------------------------------------------------
    # Repo matching
    # ------------------------------------------------------------------

    def _match_repos(
        self, rule: object, repos: list[Repository]
    ) -> list[Repository]:
        if isinstance(rule, FullNameMatcher):
            pattern = rule.full_name
            # Use fnmatch only if there's a glob character, else exact match
            if "*" in pattern or "?" in pattern or "[" in pattern:
                return [r for r in repos if fnmatch.fnmatch(r.full_name or "", pattern)]
            return [r for r in repos if r.full_name == pattern]

        if isinstance(rule, TopicsMatcher):
            wanted = set(rule.topics.any)
            return [r for r in repos if wanted & set(r.topics or [])]

        if isinstance(rule, GithubPropertyMatcher):
            name = rule.github_property.name
            value = rule.github_property.value
            result = []
            for r in repos:
                custom = (r.raw or {}).get("custom_properties", {})
                if custom.get(name) == value:
                    result.append(r)
            return result

        logger.warning("Unknown repo matcher type: %s", type(rule).__name__)
        return []

    # ------------------------------------------------------------------
    # Account matching
    # ------------------------------------------------------------------

    def _match_accounts(
        self, rule: object, accounts: list[CloudAccount]
    ) -> list[CloudAccount]:
        if isinstance(rule, AccountIdMatcher):
            return [a for a in accounts if a.id == rule.id]

        if isinstance(rule, AccountTagsMatcher):
            return [
                a for a in accounts
                if all((a.tags or {}).get(k) == v for k, v in rule.tags.items())
            ]

        if isinstance(rule, AccountFieldMatcher):
            result = []
            for a in accounts:
                if rule.environment is not None and a.environment != rule.environment:
                    continue
                if rule.provider is not None and a.provider != rule.provider:
                    continue
                if rule.name is not None and a.name != rule.name:
                    continue
                result.append(a)
            return result

        logger.warning("Unknown account matcher type: %s", type(rule).__name__)
        return []

    # ------------------------------------------------------------------
    # Lazy store loading
    # ------------------------------------------------------------------

    def _get_repos(self) -> list[Repository]:
        if self._repos is None:
            self._repos = self._store.query(Repository, {})
            logger.debug("Catalog matcher: loaded %d repos from store", len(self._repos))
        return self._repos

    def _get_accounts(self) -> list[CloudAccount]:
        if self._accounts is None:
            self._accounts = self._store.query(CloudAccount, {})
            logger.debug(
                "Catalog matcher: loaded %d cloud accounts from store", len(self._accounts)
            )
        return self._accounts

    # ------------------------------------------------------------------
    # Human-readable rule descriptions
    # ------------------------------------------------------------------

    @staticmethod
    def _describe_repo_rule(rule: object, idx: int) -> str:
        if isinstance(rule, FullNameMatcher):
            return f"repos[{idx}].full_name={rule.full_name!r}"
        if isinstance(rule, TopicsMatcher):
            return f"repos[{idx}].topics.any={rule.topics.any!r}"
        if isinstance(rule, GithubPropertyMatcher):
            return (
                f"repos[{idx}].github_property"
                f"({rule.github_property.name!r}={rule.github_property.value!r})"
            )
        return f"repos[{idx}].{type(rule).__name__}"

    @staticmethod
    def _describe_account_rule(rule: object, idx: int) -> str:
        if isinstance(rule, AccountIdMatcher):
            return f"accounts[{idx}].id={rule.id!r}"
        if isinstance(rule, AccountTagsMatcher):
            return f"accounts[{idx}].tags={rule.tags!r}"
        if isinstance(rule, AccountFieldMatcher):
            fields = {
                k: v for k, v in {
                    "environment": rule.environment,
                    "provider": rule.provider,
                    "name": rule.name,
                }.items() if v is not None
            }
            return f"accounts[{idx}].fields={fields!r}"
        return f"accounts[{idx}].{type(rule).__name__}"
