from gitventory.store.base import AbstractStore


def create_store(config) -> AbstractStore:
    """Factory: instantiate the correct store backend from config."""
    backend = config.backend

    if backend == "sqlite":
        from gitventory.store.sqlite import SQLiteStore
        return SQLiteStore(config.sqlite.path)

    if backend == "json":
        from gitventory.store.json_store import FlatJsonStore
        return FlatJsonStore(config.json_store.directory)

    if backend == "postgres":
        from gitventory.store.postgres import PostgresStore
        return PostgresStore(config.postgres.url)

    raise ValueError(f"Unknown store backend: {backend!r}. Valid: sqlite, json, postgres")


__all__ = ["AbstractStore", "create_store"]
