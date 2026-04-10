"""Adapter registry — maps adapter names to their classes."""

from __future__ import annotations

from typing import Type

from gitventory.adapters.base import AbstractAdapter

_REGISTRY: dict[str, Type[AbstractAdapter]] = {}


def register_adapter(cls: Type[AbstractAdapter]) -> Type[AbstractAdapter]:
    """Class decorator that registers an adapter by its ``ADAPTER_NAME``."""
    name = cls.ADAPTER_NAME
    if name in _REGISTRY:
        raise ValueError(
            f"Adapter name {name!r} is already registered by {_REGISTRY[name].__qualname__}. "
            f"Cannot register {cls.__qualname__} under the same name."
        )
    _REGISTRY[name] = cls
    return cls


def get_adapter(name: str) -> Type[AbstractAdapter]:
    """Look up a registered adapter class by name."""
    if name not in _REGISTRY:
        raise KeyError(
            f"No adapter registered for {name!r}. "
            f"Known adapters: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def list_adapters() -> list[str]:
    """Return the sorted list of all registered adapter names."""
    return sorted(_REGISTRY)


def get_registry() -> dict[str, Type[AbstractAdapter]]:
    """Return a copy of the full registry (name → class)."""
    return dict(_REGISTRY)
