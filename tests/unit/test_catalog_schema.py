"""Unit tests for catalog YAML schema parsing and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gitventory.catalog.schema import (
    AccountFieldMatcher,
    AccountIdMatcher,
    AccountTagsMatcher,
    CatalogFile,
    FullNameMatcher,
    GithubPropertyMatcher,
    TopicsMatcher,
    _parse_account_matcher,
    _parse_repo_matcher,
)


# ---------------------------------------------------------------------------
# CatalogFile — top-level parsing
# ---------------------------------------------------------------------------

def test_empty_catalog_file():
    cfg = CatalogFile()
    assert cfg.catalog.entity_types == []
    assert cfg.catalog.entities == []


def test_catalog_file_parses_entity_types():
    cfg = CatalogFile(**{
        "catalog": {
            "entity_types": [
                {"id": "service", "display_name": "Service"},
                {"id": "project"},
            ],
            "entities": [],
        }
    })
    assert len(cfg.catalog.entity_types) == 2
    assert cfg.catalog.entity_types[0].id == "service"
    # Default display_name derived from id
    assert cfg.catalog.entity_types[1].display_name == "Project"


def test_catalog_entity_references_valid_type():
    cfg = CatalogFile(**{
        "catalog": {
            "entity_types": [{"id": "service"}],
            "entities": [{"id": "my-api", "type": "service", "display_name": "My API"}],
        }
    })
    assert cfg.catalog.entities[0].id == "my-api"
    assert cfg.catalog.entities[0].type == "service"


def test_catalog_entity_unknown_type_raises():
    with pytest.raises(ValidationError, match="unknown type"):
        CatalogFile(**{
            "catalog": {
                "entity_types": [{"id": "service"}],
                "entities": [{"id": "bad", "type": "nonexistent"}],
            }
        })


def test_catalog_entity_default_display_name():
    cfg = CatalogFile(**{
        "catalog": {
            "entity_types": [{"id": "service"}],
            "entities": [{"id": "checkout-api", "type": "service"}],
        }
    })
    # "checkout-api" → replace("-", " ") → "checkout api" → title() → "Checkout Api"
    assert cfg.catalog.entities[0].display_name == "Checkout Api"


def test_catalog_entity_properties_stored():
    cfg = CatalogFile(**{
        "catalog": {
            "entity_types": [{"id": "service"}],
            "entities": [{
                "id": "svc",
                "type": "service",
                "properties": {"criticality": "critical", "runbook_url": "https://..."},
            }],
        }
    })
    assert cfg.catalog.entities[0].properties["criticality"] == "critical"


def test_catalog_type_display_name_lookup():
    cfg = CatalogFile(**{
        "catalog": {
            "entity_types": [{"id": "service", "display_name": "Service"}],
            "entities": [],
        }
    })
    assert cfg.catalog.type_display_name("service") == "Service"
    # Unknown type returns title-cased id
    assert cfg.catalog.type_display_name("unknown") == "Unknown"


# ---------------------------------------------------------------------------
# Repo matchers
# ---------------------------------------------------------------------------

def test_parse_repo_matcher_full_name():
    m = _parse_repo_matcher({"full_name": "my-org/checkout-api"})
    assert isinstance(m, FullNameMatcher)
    assert m.full_name == "my-org/checkout-api"


def test_parse_repo_matcher_full_name_glob():
    m = _parse_repo_matcher({"full_name": "my-org/checkout-*"})
    assert isinstance(m, FullNameMatcher)
    assert "*" in m.full_name


def test_parse_repo_matcher_topics():
    m = _parse_repo_matcher({"topics": {"any": ["checkout", "payments"]}})
    assert isinstance(m, TopicsMatcher)
    assert m.topics.any == ["checkout", "payments"]


def test_parse_repo_matcher_github_property():
    m = _parse_repo_matcher({"github_property": {"name": "service", "value": "checkout"}})
    assert isinstance(m, GithubPropertyMatcher)
    assert m.github_property.name == "service"
    assert m.github_property.value == "checkout"


def test_github_property_name_rejects_special_chars():
    with pytest.raises(ValidationError):
        _parse_repo_matcher({"github_property": {"name": "bad/../name", "value": "x"}})


def test_parse_repo_matcher_unknown_key_raises():
    with pytest.raises(ValueError):
        _parse_repo_matcher({"unknown_key": "value"})


# ---------------------------------------------------------------------------
# Account matchers
# ---------------------------------------------------------------------------

def test_parse_account_matcher_id():
    m = _parse_account_matcher({"id": "aws:123456789012"})
    assert isinstance(m, AccountIdMatcher)
    assert m.id == "aws:123456789012"


def test_parse_account_matcher_tags():
    m = _parse_account_matcher({"tags": {"service": "checkout", "env": "prod"}})
    assert isinstance(m, AccountTagsMatcher)
    assert m.tags == {"service": "checkout", "env": "prod"}


def test_parse_account_matcher_environment():
    m = _parse_account_matcher({"environment": "prod"})
    assert isinstance(m, AccountFieldMatcher)
    assert m.environment == "prod"


def test_parse_account_matcher_provider():
    m = _parse_account_matcher({"provider": "aws"})
    assert isinstance(m, AccountFieldMatcher)
    assert m.provider == "aws"


def test_parse_account_matcher_unknown_field_raises():
    with pytest.raises(ValidationError, match="Unknown account matcher field"):
        _parse_account_matcher({"arbitrary_key": "value"})


# ---------------------------------------------------------------------------
# Matchers inside a full entity
# ---------------------------------------------------------------------------

def test_entity_matchers_parsed_correctly():
    cfg = CatalogFile(**{
        "catalog": {
            "entity_types": [{"id": "service"}],
            "entities": [{
                "id": "checkout-api",
                "type": "service",
                "matchers": {
                    "repos": [
                        {"full_name": "my-org/checkout-api"},
                        {"topics": {"any": ["checkout"]}},
                    ],
                    "accounts": [
                        {"id": "aws:123456789012"},
                        {"tags": {"service": "checkout"}},
                    ],
                },
            }],
        }
    })
    entity = cfg.catalog.entities[0]
    assert len(entity.matchers.repos) == 2
    assert isinstance(entity.matchers.repos[0], FullNameMatcher)
    assert isinstance(entity.matchers.repos[1], TopicsMatcher)
    assert len(entity.matchers.accounts) == 2
    assert isinstance(entity.matchers.accounts[0], AccountIdMatcher)
    assert isinstance(entity.matchers.accounts[1], AccountTagsMatcher)
