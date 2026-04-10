"""Abstract adapter interface — the contract every collector must fulfil."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Type

from pydantic import BaseModel

from gitventory.models.base import InventoryEntity


class AdapterConfig(BaseModel):
    """Base configuration class all adapter configs inherit from."""
    enabled: bool = True


class AbstractAdapter(ABC):
    """
    Base class for all inventory collectors.

    Adapter responsibilities:
    - Declare ``ADAPTER_NAME`` (used as config key and log label)
    - Declare ``CONFIG_CLASS`` (Pydantic model for adapter-specific config)
    - Implement ``collect()`` — a lazy iterator that yields InventoryEntity instances

    Adapters must NOT know about the store.  They produce entities; the runner
    and store handle persistence.  This makes adapters testable in isolation.

    Registration:
    Apply the ``@register_adapter`` decorator from ``gitventory.registry`` to
    any concrete subclass, then import it in ``gitventory/adapters/__init__.py``.
    """

    ADAPTER_NAME: str
    CONFIG_CLASS: Type[AdapterConfig]

    def __init__(self, config: AdapterConfig) -> None:
        self.config = config

    @abstractmethod
    def collect(self) -> Iterator[InventoryEntity]:
        """
        Yield InventoryEntity instances one at a time.

        - Use ``yield`` so the iterator is lazy and never holds the full
          collection in memory.
        - Raise ``StopIteration`` (or just return) when done.
        - Any other exception propagates to the runner, which catches it,
          logs it, and marks the collection run as failed/partial.
        """
        ...

    def validate_connectivity(self) -> bool:
        """
        Optional pre-flight check — test credentials / network before the full
        collection.  Return ``True`` to proceed, ``False`` to abort.
        Default implementation always returns True.
        """
        return True
