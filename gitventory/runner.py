"""CollectionRunner — orchestrates config → adapters → store."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from gitventory.adapters.base import AbstractAdapter
from gitventory.config import AppConfig
from gitventory.registry import get_adapter, get_registry
from gitventory.store.base import AbstractStore

logger = logging.getLogger(__name__)


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
        """
        # Ensure all adapter modules are imported (triggers @register_adapter)
        import gitventory.adapters  # noqa: F401

        enabled = self.config.adapters.enabled_adapters()
        results: dict[str, int] = {}

        for name, adapter_cfg in enabled.items():
            if adapter_names and name not in adapter_names:
                continue

            try:
                adapter_cls = get_adapter(name)
            except KeyError:
                logger.warning("Adapter %r is enabled in config but not registered; skipping.", name)
                continue

            adapter: AbstractAdapter = adapter_cls(adapter_cfg)
            started_at = datetime.now(timezone.utc)
            logger.info("Starting adapter: %s%s", name, " (dry-run)" if dry_run else "")

            try:
                if validate and not adapter.validate_connectivity():
                    raise RuntimeError(f"Connectivity check failed for adapter {name!r}")

                if dry_run:
                    count = sum(1 for _ in adapter.collect())
                else:
                    count = self.store.upsert_many(adapter.collect())

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

        return results
