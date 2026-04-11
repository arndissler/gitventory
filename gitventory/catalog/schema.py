"""Pydantic v2 schemas for the catalog YAML file.

These schemas are intentionally permissive — they accept what humans write.
The sync layer maps them to CatalogEntity / CatalogMembership instances.

Security note
-------------
Matcher field names are validated against a fixed allowlist so that no
arbitrary attribute access is possible during evaluation.  Glob patterns
are evaluated with ``fnmatch`` only — no regex, preventing ReDoS attacks.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator
from typing_extensions import Annotated


# ---------------------------------------------------------------------------
# Entity type definitions
# ---------------------------------------------------------------------------

class EntityTypeEntry(BaseModel):
    id: str
    """User-defined slug, e.g. ``service``, ``project``, ``domain``."""
    display_name: str = ""

    @model_validator(mode="after")
    def default_display_name(self) -> "EntityTypeEntry":
        if not self.display_name:
            self.display_name = self.id.replace("-", " ").title()
        return self


# ---------------------------------------------------------------------------
# Repository matchers
# ---------------------------------------------------------------------------

class FullNameMatcher(BaseModel):
    """Match repositories by ``full_name`` (exact or fnmatch glob)."""
    full_name: str

    @property
    def matcher_type(self) -> str:
        return "full_name"


class TopicsMatcher(BaseModel):
    """Match repositories that have ANY of the listed topics."""
    topics: "_TopicsRule"

    @property
    def matcher_type(self) -> str:
        return "topics"


class _TopicsRule(BaseModel):
    any: list[str] = []


class GithubPropertyMatcher(BaseModel):
    """Match repositories by a GitHub custom property value."""
    github_property: "_PropertyRule"

    @property
    def matcher_type(self) -> str:
        return "github_property"

    @field_validator("github_property", mode="before")
    @classmethod
    def validate_name(cls, v: Any) -> Any:
        if isinstance(v, dict):
            name = v.get("name", "")
            # Property names: alphanumeric, hyphens, underscores only
            if not all(c.isalnum() or c in "-_" for c in name):
                raise ValueError(
                    f"github_property name {name!r} contains disallowed characters. "
                    "Only alphanumeric characters, hyphens, and underscores are allowed."
                )
        return v


class _PropertyRule(BaseModel):
    name: str
    value: str


# Discriminated union: at least one of the unique keys must be present.
# We use a plain Union (not Annotated discriminated) since the fields differ.
RepoMatcher = Union[FullNameMatcher, TopicsMatcher, GithubPropertyMatcher]


def _parse_repo_matcher(raw: Any) -> RepoMatcher:
    """Parse a raw dict into the correct RepoMatcher subtype."""
    if not isinstance(raw, dict):
        raise ValueError(f"Repo matcher must be a dict, got {type(raw).__name__}")
    if "full_name" in raw:
        return FullNameMatcher(**raw)
    if "topics" in raw:
        return TopicsMatcher(**raw)
    if "github_property" in raw:
        return GithubPropertyMatcher(**raw)
    raise ValueError(f"Unrecognised repo matcher keys: {list(raw.keys())}")


# ---------------------------------------------------------------------------
# Cloud account matchers
# ---------------------------------------------------------------------------

class AccountIdMatcher(BaseModel):
    """Match a cloud account by its stable ID (e.g. ``aws:123456789012``)."""
    id: str

    @property
    def matcher_type(self) -> str:
        return "id"


class AccountTagsMatcher(BaseModel):
    """Match cloud accounts that have ALL of the specified tag key-value pairs."""
    tags: dict[str, str]

    @property
    def matcher_type(self) -> str:
        return "tags"


class AccountFieldMatcher(BaseModel):
    """Match cloud accounts by a direct field value (e.g. ``environment: prod``).

    Allowed fields: ``environment``, ``provider``, ``name``.
    """

    _ALLOWED_FIELDS: ClassVar[frozenset[str]] = frozenset({"environment", "provider", "name"})

    environment: Optional[str] = None
    provider: Optional[str] = None
    name: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def check_known_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            unknown = set(data.keys()) - cls._ALLOWED_FIELDS
            if unknown:
                raise ValueError(
                    f"Unknown account matcher field(s): {unknown}. "
                    f"Allowed: {sorted(cls._ALLOWED_FIELDS)}"
                )
        return data

    @property
    def matcher_type(self) -> str:
        return "field"


AccountMatcher = Union[AccountIdMatcher, AccountTagsMatcher, AccountFieldMatcher]


def _parse_account_matcher(raw: Any) -> AccountMatcher:
    """Parse a raw dict into the correct AccountMatcher subtype."""
    if not isinstance(raw, dict):
        raise ValueError(f"Account matcher must be a dict, got {type(raw).__name__}")
    if "id" in raw:
        return AccountIdMatcher(**raw)
    if "tags" in raw:
        return AccountTagsMatcher(**raw)
    # Try field matcher (environment, provider, name)
    return AccountFieldMatcher(**raw)


# ---------------------------------------------------------------------------
# Matchers container per entity
# ---------------------------------------------------------------------------

class MatchersEntry(BaseModel):
    repos: list[RepoMatcher] = []
    accounts: list[AccountMatcher] = []

    @model_validator(mode="before")
    @classmethod
    def parse_matchers(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        result = {}
        if "repos" in data:
            result["repos"] = [_parse_repo_matcher(r) for r in data["repos"]]
        if "accounts" in data:
            result["accounts"] = [_parse_account_matcher(a) for a in data["accounts"]]
        return result


# ---------------------------------------------------------------------------
# Catalog entity entry
# ---------------------------------------------------------------------------

class CatalogEntityEntry(BaseModel):
    id: str
    """Stable slug within the type namespace, e.g. ``checkout-api``."""
    type: str
    """Must match an ``id`` in the ``entity_types`` list."""
    display_name: str = ""
    description: Optional[str] = None
    owning_team: Optional[str] = None
    """Team slug (without ``team:`` prefix) — resolved on ingest."""
    properties: dict[str, Any] = {}
    matchers: MatchersEntry = MatchersEntry()

    @model_validator(mode="after")
    def default_display_name(self) -> "CatalogEntityEntry":
        if not self.display_name:
            self.display_name = self.id.replace("-", " ").title()
        return self


# ---------------------------------------------------------------------------
# Top-level catalog file
# ---------------------------------------------------------------------------

class CatalogContent(BaseModel):
    entity_types: list[EntityTypeEntry] = []
    entities: list[CatalogEntityEntry] = []

    @model_validator(mode="after")
    def validate_entity_types(self) -> "CatalogContent":
        """Every entity's type must reference a declared entity_type."""
        known = {et.id for et in self.entity_types}
        for entity in self.entities:
            if entity.type not in known:
                raise ValueError(
                    f"Entity {entity.id!r} references unknown type {entity.type!r}. "
                    f"Declared types: {sorted(known)}"
                )
        return self

    def type_display_name(self, type_id: str) -> str:
        for et in self.entity_types:
            if et.id == type_id:
                return et.display_name
        return type_id.title()


class CatalogFile(BaseModel):
    """Root schema for ``catalog.yaml``."""
    catalog: CatalogContent = CatalogContent()
