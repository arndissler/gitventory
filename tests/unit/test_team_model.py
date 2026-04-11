"""Unit tests for the Team model and ExternalIdentity."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gitventory.models.team import ExternalIdentity, Team


_NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)

def _make_team(**kwargs) -> Team:
    defaults = dict(
        id="team:platform-engineering",
        provider_id="platform-engineering",
        source_adapter="static_yaml",
        collected_at=_NOW,
        display_name="Platform Engineering",
    )
    defaults.update(kwargs)
    return Team(**defaults)


# ---------------------------------------------------------------------------
# ExternalIdentity
# ---------------------------------------------------------------------------

def test_external_identity_minimal():
    ident = ExternalIdentity(provider="github_team", value="my-org/platform-engineering")
    assert ident.provider == "github_team"
    assert ident.value == "my-org/platform-engineering"
    assert ident.metadata == {}


def test_external_identity_with_metadata():
    ident = ExternalIdentity(
        provider="entraid_group",
        value="aaaa-bbbb-cccc",
        metadata={"display_name": "Platform Eng AD Group"},
    )
    assert ident.metadata["display_name"] == "Platform Eng AD Group"


def test_external_identity_unknown_provider_accepted():
    # Free-form provider — no validation constraint
    ident = ExternalIdentity(provider="custom_idp", value="some-id")
    assert ident.provider == "custom_idp"


# ---------------------------------------------------------------------------
# Team — new fields
# ---------------------------------------------------------------------------

def test_team_defaults():
    team = _make_team()
    assert team.type_id == "team"
    assert team.identities == []
    assert team.contacts == {}
    assert team.properties == {}


def test_team_type_id():
    team = _make_team(type_id="squad")
    assert team.type_id == "squad"


def test_team_identities():
    team = _make_team(
        identities=[
            ExternalIdentity(provider="github_team", value="my-org/platform-engineering"),
            ExternalIdentity(provider="entraid_group", value="aaaa-bbbb"),
        ]
    )
    assert len(team.identities) == 2
    assert team.identities[0].provider == "github_team"
    assert team.identities[1].provider == "entraid_group"


def test_team_contacts():
    team = _make_team(contacts={"slack_channel": "#platform-eng", "jira_project": "PLAT"})
    assert team.contacts["slack_channel"] == "#platform-eng"
    assert team.contacts["jira_project"] == "PLAT"


def test_team_properties():
    team = _make_team(properties={"cost_center": "1234", "location": "Berlin"})
    assert team.properties["cost_center"] == "1234"


# ---------------------------------------------------------------------------
# Backwards compatibility — old fields still work
# ---------------------------------------------------------------------------

def test_legacy_fields_still_work():
    team = _make_team(
        email="platform@example.com",
        slack_channel="#platform-eng",
        github_team_slug="platform-engineering",
        members=["alice", "bob"],
    )
    assert team.email == "platform@example.com"
    assert team.slack_channel == "#platform-eng"
    assert team.github_team_slug == "platform-engineering"
    assert team.members == ["alice", "bob"]
    # New fields still default correctly
    assert team.identities == []
    assert team.type_id == "team"


def test_team_model_dump_includes_new_fields():
    team = _make_team(
        type_id="guild",
        identities=[ExternalIdentity(provider="github_team", value="my-org/design-guild")],
        contacts={"slack_channel": "#design"},
        properties={"focus": "UX"},
    )
    data = team.model_dump()
    assert data["type_id"] == "guild"
    assert len(data["identities"]) == 1
    assert data["identities"][0]["provider"] == "github_team"
    assert data["contacts"]["slack_channel"] == "#design"
    assert data["properties"]["focus"] == "UX"
