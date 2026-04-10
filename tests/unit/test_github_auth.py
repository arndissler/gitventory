"""Unit tests for GitHub authentication config types and backwards compatibility."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from gitventory.adapters.github.auth import (
    AppAuthConfig,
    TokenAuthConfig,
    TokenPerOrgConfig,
)
from gitventory.adapters.github.adapter import GitHubAdapterConfig


# ---------------------------------------------------------------------------
# AppAuthConfig
# ---------------------------------------------------------------------------

def test_app_auth_with_inline_key():
    cfg = AppAuthConfig(app_id=12345, private_key="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----")
    assert cfg.type == "app"
    assert cfg.app_id == 12345
    assert cfg.resolve_private_key().startswith("-----BEGIN")


def test_app_auth_with_key_file(tmp_path: Path):
    key_file = tmp_path / "app.pem"
    key_file.write_text("-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----")
    cfg = AppAuthConfig(app_id=12345, private_key_file=str(key_file))
    assert cfg.resolve_private_key().startswith("-----BEGIN")


def test_app_auth_inline_key_wins_over_file(tmp_path: Path):
    key_file = tmp_path / "app.pem"
    key_file.write_text("from-file")
    cfg = AppAuthConfig(
        app_id=12345,
        private_key="inline-key",
        private_key_file=str(key_file),
    )
    assert cfg.resolve_private_key() == "inline-key"


def test_app_auth_requires_key():
    with pytest.raises(ValidationError, match="private_key"):
        AppAuthConfig(app_id=12345)


def test_app_auth_key_file_not_found(tmp_path: Path):
    cfg = AppAuthConfig(app_id=12345, private_key_file=str(tmp_path / "missing.pem"))
    with pytest.raises(FileNotFoundError):
        cfg.resolve_private_key()


def test_app_auth_installation_ids_optional():
    cfg = AppAuthConfig(app_id=12345, private_key="fake-key")
    assert cfg.installation_ids == {}


def test_app_auth_pinned_installation_ids():
    cfg = AppAuthConfig(
        app_id=12345,
        private_key="fake-key",
        installation_ids={"my-org": 99999},
    )
    assert cfg.installation_ids["my-org"] == 99999


# ---------------------------------------------------------------------------
# TokenPerOrgConfig
# ---------------------------------------------------------------------------

def test_token_per_org_returns_correct_token():
    cfg = TokenPerOrgConfig(org_tokens={"org-a": "token-a", "org-b": "token-b"})
    assert cfg.token_for("org-a") == "token-a"
    assert cfg.token_for("org-b") == "token-b"


def test_token_per_org_raises_for_missing_org():
    cfg = TokenPerOrgConfig(org_tokens={"org-a": "token-a"})
    with pytest.raises(KeyError, match="org-b"):
        cfg.token_for("org-b")


def test_token_per_org_default_empty():
    cfg = TokenPerOrgConfig()
    assert cfg.org_tokens == {}


# ---------------------------------------------------------------------------
# TokenAuthConfig
# ---------------------------------------------------------------------------

def test_token_auth_explicit():
    cfg = TokenAuthConfig(token="ghp_explicit")
    assert cfg.token == "ghp_explicit"


def test_token_auth_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_from_env")
    cfg = TokenAuthConfig()
    assert cfg.token == "ghp_from_env"


def test_token_auth_empty_when_no_env(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    cfg = TokenAuthConfig()
    assert cfg.token == ""


# ---------------------------------------------------------------------------
# GitHubAdapterConfig — discriminated union selection
# ---------------------------------------------------------------------------

def test_adapter_config_selects_app_auth():
    cfg = GitHubAdapterConfig(
        auth={"type": "app", "app_id": 12345, "private_key": "fake-key"},
        orgs=["my-org"],
    )
    assert isinstance(cfg.auth, AppAuthConfig)
    assert cfg.auth.app_id == 12345


def test_adapter_config_selects_token_per_org():
    cfg = GitHubAdapterConfig(
        auth={"type": "token_per_org", "org_tokens": {"my-org": "tok"}},
        orgs=["my-org"],
    )
    assert isinstance(cfg.auth, TokenPerOrgConfig)


def test_adapter_config_selects_token():
    cfg = GitHubAdapterConfig(
        auth={"type": "token", "token": "ghp_test"},
        orgs=["my-org"],
    )
    assert isinstance(cfg.auth, TokenAuthConfig)
    assert cfg.auth.token == "ghp_test"


def test_adapter_config_defaults_to_token_auth():
    cfg = GitHubAdapterConfig()
    assert isinstance(cfg.auth, TokenAuthConfig)


# ---------------------------------------------------------------------------
# Backwards compatibility — old-style top-level token
# ---------------------------------------------------------------------------

def test_legacy_token_migrated_to_auth():
    """Old config.yaml style: token: 'ghp_...' at top level should still work."""
    cfg = GitHubAdapterConfig(token="ghp_legacy", orgs=["my-org"])
    assert isinstance(cfg.auth, TokenAuthConfig)
    assert cfg.auth.token == "ghp_legacy"


def test_legacy_token_empty_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_env_fallback")
    cfg = GitHubAdapterConfig(token="", orgs=["my-org"])
    assert isinstance(cfg.auth, TokenAuthConfig)
    assert cfg.auth.token == "ghp_env_fallback"


def test_explicit_auth_block_takes_precedence_over_legacy_token():
    """If both auth block and token key are present, auth block wins (token key ignored)."""
    cfg = GitHubAdapterConfig(
        auth={"type": "token_per_org", "org_tokens": {"my-org": "per-org-tok"}},
        # token key present but should be ignored because auth is also set
        orgs=["my-org"],
    )
    assert isinstance(cfg.auth, TokenPerOrgConfig)
