"""CollectionRunner — orchestrates config → adapters → store."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterator, Optional

from pydantic import ValidationError

from gitventory.adapters.base import AbstractAdapter
from gitventory.config import AppConfig
from gitventory.models.base import InventoryEntity
from gitventory.registry import get_adapter, get_registry
from gitventory.store.base import AbstractStore

logger = logging.getLogger(__name__)


def _guarded_iter(
    entity_iter: Iterator[InventoryEntity],
    adapter_name: str,
    max_errors: int,
) -> Iterator[InventoryEntity]:
    """Yield entities from *entity_iter*, tolerating up to *max_errors* ValidationErrors.

    Parameters
    ----------
    max_errors:
        0  — strict: re-raise on the first error (preserves current behaviour).
        N  — tolerate up to N errors, then re-raise.
        -1 — fully lenient: always warn and skip, never re-raise.
    """
    error_count = 0
    it = iter(entity_iter)
    limit_str = str(max_errors) if max_errors >= 0 else "unlimited"
    while True:
        try:
            yield next(it)
        except StopIteration:
            break
        except ValidationError as exc:
            error_count += 1
            logger.warning(
                "Adapter %s: skipping entity due to validation error "
                "(%d/%s): %s",
                adapter_name, error_count, limit_str, exc,
            )
            if max_errors == 0 or (max_errors > 0 and error_count > max_errors):
                raise


class CollectionRunner:
    """Drives one or more adapters and persists the results into the store."""

    def __init__(self, config: AppConfig, store: AbstractStore) -> None:
        self.config = config
        self.store = store

    def run(
        self,
        adapter_names: Optional[list[str]] = None,
        dry_run: bool = False,
        validate: bool = True,
        repo: Optional[str] = None,
        max_entity_errors: Optional[int] = None,
    ) -> dict[str, int]:
        """
        Run enabled adapters and return a map of ``{adapter_name: entity_count}``.

        Parameters
        ----------
        adapter_names:
            If given, only these adapters are executed.  Otherwise all enabled
            adapters in the config are run.
        dry_run:
            Collect entities but do not write to the store.  Returns counts as
            if the write had happened.
        validate:
            Call ``adapter.validate_connectivity()`` before collecting.
        repo:
            If given, collect only this repository (``org/name`` full name).
            Adapters that do not support single-repo collection are skipped.
        """
        # Ensure all adapter modules are imported (triggers @register_adapter)
        import gitventory.adapters  # noqa: F401

        enabled = self.config.adapters.enabled_adapters()
        results: dict[str, int] = {}
        github_adapter = None  # held so we can call get_collected_orgs() after the run

        for name, adapter_cfg in enabled.items():
            if adapter_names and name not in adapter_names:
                continue

            try:
                adapter_cls = get_adapter(name)
            except KeyError:
                logger.warning("Adapter %r is enabled in config but not registered; skipping.", name)
                continue

            adapter: AbstractAdapter = adapter_cls(adapter_cfg)
            if name == "github":
                github_adapter = adapter

            if repo and not hasattr(adapter, "collect_one"):
                logger.debug(
                    "Adapter %r does not support single-repo collection; skipping.", name
                )
                continue

            started_at = datetime.now(timezone.utc)
            logger.info(
                "Starting adapter: %s%s%s",
                name,
                f" (repo={repo!r})" if repo else "",
                " (dry-run)" if dry_run else "",
            )

            try:
                if validate and not adapter.validate_connectivity():
                    raise RuntimeError(f"Connectivity check failed for adapter {name!r}")

                raw_iter = adapter.collect_one(repo) if repo else adapter.collect()  # type: ignore[attr-defined]
                max_err = max_entity_errors if max_entity_errors is not None else getattr(adapter_cfg, "max_entity_errors", 10)
                entity_iter = _guarded_iter(raw_iter, name, max_err)

                if dry_run:
                    count = sum(1 for _ in entity_iter)
                else:
                    count = self.store.upsert_many(entity_iter)

                finished_at = datetime.now(timezone.utc)
                elapsed = (finished_at - started_at).total_seconds()
                logger.info(
                    "Adapter %s completed: %d entities in %.1fs",
                    name, count, elapsed,
                )
                if not dry_run:
                    self.store.record_collection_run(
                        name, started_at, finished_at, count, "success"
                    )
                results[name] = count

            except Exception as exc:
                finished_at = datetime.now(timezone.utc)
                logger.error("Adapter %s failed: %s", name, exc, exc_info=True)
                if not dry_run:
                    self.store.record_collection_run(
                        name, started_at, finished_at, 0, "failed", str(exc)
                    )
                results[name] = 0

        if not dry_run and self.config.catalog.file:
            self._run_catalog_sync()

        if not dry_run and self.config.adapters.github and self.config.adapters.github.enabled:
            self._run_ownership_sync()
            self._run_team_enrichment_sync()
            if github_adapter is not None and repo is None:
                # Full-org run: prune stale team assignments
                self._run_stale_cleanup(github_adapter)

        static_cfg = getattr(self.config.adapters, "static_yaml", None)
        if not dry_run and static_cfg and getattr(static_cfg, "users_file", None):
            self._run_user_enrichment_sync(static_cfg.users_file)

        return results

    def _run_catalog_sync(self) -> None:
        """Evaluate catalog matchers after all adapters have run."""
        from gitventory.catalog.sync import CatalogSyncer
        try:
            syncer = CatalogSyncer(self.config.catalog.file, self.store)  # type: ignore[arg-type]
            counts = syncer.sync(clear=False)
            logger.info(
                "Catalog sync: %d entities, %d memberships",
                counts["entities"], counts["memberships"],
            )
        except Exception as exc:
            logger.error("Catalog sync failed: %s", exc, exc_info=True)

    def _run_ownership_sync(self) -> None:
        """Assign owning_team_id on repositories from GitHub team membership."""
        from gitventory.ownership.sync import OwnershipSyncer
        try:
            syncer = OwnershipSyncer(self.config.adapters.github, self.store)  # type: ignore[arg-type]
            counts = syncer.sync(force=False)
            logger.info(
                "Ownership sync: %d repos updated across %d teams",
                counts["repos_updated"], counts["teams_processed"],
            )
        except Exception as exc:
            logger.error("Ownership sync failed: %s", exc, exc_info=True)

    def _run_team_enrichment_sync(self) -> None:
        """Copy contact info from YAML teams onto their GitHub-discovered counterparts."""
        from gitventory.ownership.team_enrichment import TeamEnrichmentSyncer
        try:
            syncer = TeamEnrichmentSyncer(self.store)
            counts = syncer.sync()
            logger.info("Team enrichment sync: %d teams enriched", counts["teams_enriched"])
        except Exception as exc:
            logger.error("Team enrichment sync failed: %s", exc, exc_info=True)

    def _run_user_enrichment_sync(self, users_file: str) -> None:
        """Patch email/Slack/properties from users.yaml onto discovered User records."""
        from gitventory.ownership.user_enrichment import UserEnrichmentSyncer
        try:
            syncer = UserEnrichmentSyncer(users_file, self.store)
            counts = syncer.sync()
            logger.info(
                "User enrichment sync: %d enriched, %d unmatched",
                counts["users_enriched"], counts["unmatched_refs"],
            )
        except Exception as exc:
            logger.error("User enrichment sync failed: %s", exc, exc_info=True)

    def _run_stale_cleanup(self, github_adapter) -> None:  # type: ignore[no-untyped-def]
        """Delete RepoTeamAssignment rows for orgs where we just ran a full collection.

        Rows with ``collected_at`` older than the org's collection start time
        represent team access that has since been revoked.
        """
        from gitventory.models.repo_team_assignment import RepoTeamAssignment
        try:
            collected_orgs = github_adapter.get_collected_orgs()
            total_deleted = 0
            for org, run_start in collected_orgs.items():
                deleted = self.store.delete_stale_rows(
                    RepoTeamAssignment, "org", org, run_start
                )
                if deleted:
                    logger.debug(
                        "Stale cleanup: deleted %d stale RepoTeamAssignment rows for org %r",
                        deleted, org,
                    )
                total_deleted += deleted
            if total_deleted:
                logger.info("Stale cleanup: %d stale team assignment rows removed", total_deleted)
        except Exception as exc:
            logger.error("Stale assignment cleanup failed: %s", exc, exc_info=True)
