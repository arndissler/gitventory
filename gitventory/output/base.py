"""Abstract formatter — extensible base for CLI and future Web UI output."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AbstractFormatter(ABC):
    """Base class for output formatters."""

    @abstractmethod
    def format(self, data: list[Any], fields: list[str], title: str) -> str:
        """Format a list of objects into a string representation."""
        ...
