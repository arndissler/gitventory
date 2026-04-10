"""Unit tests for inventory entity models — focus on stable ID behaviour."""

from datetime import datetime, timezone

import pytest

from gitventory.models import (
    CloudAccount,
    DeploymentMapping,
    GhasAlert,
    Repository,
    Team,
)

NOW = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

def make_repo(**kwargs) -> Repository:
    defaults = dict(
        id="github:12345678",
        provider_id="12345678",
        provider="github",
        source_adapter="github",
        collected_at=NOW,
        org="my-org",
        name="my-repo",
        full_name="my-org/my-repo",
        url="https://github.com/my-org/my-repo",
        default_branch="main",
    )
    defaults.update(kwargs)
    return Repository(**defaults)


def test_repository_stable_id_survives_rename():
    """Renaming full_name must not affect the stable id."""
    repo = make_repo()
    assert repo.id == "github:12345678"

    # Simulate a rename — only full_name changes
    renamed = repo.model_copy(update={"full_name": "my-org/new-name"})
    assert renamed.id == "github:12345678"
    assert renamed.full_name == "my-org/new-name"


def test_repository_has_open_alerts_false_by_default():
    repo = make_repo()
    assert not repo.has_open_alerts


def test_repository_has_open_alerts_true_when_secret_alert():
    repo = make_repo(open_secret_alerts=1)
    assert repo.has_open_alerts


def test_repository_has_open_alerts_true_when_dependabot():
    repo = make_repo(open_dependabot_alerts=3)
    assert repo.has_open_alerts


def test_repository_topics_default_empty():
    repo = make_repo()
    assert repo.topics == []


def test_repository_collected_at_naive_becomes_utc():
    naive = datetime(2026, 4, 10, 12, 0, 0)  # no tzinfo
    repo = make_repo(collected_at=naive)
    assert repo.collected_at.tzinfo is not None


# ---------------------------------------------------------------------------
# CloudAccount
# ---------------------------------------------------------------------------

def make_account(**kwargs) -> CloudAccount:
    defaults = dict(
        id="aws:123456789012",
        provider_id="123456789012",
        provider="aws",
        source_adapter="static_yaml",
        collected_at=NOW,
        name="prod-platform",
    )
    defaults.update(kwargs)
    return CloudAccount(**defaults)


def test_cloud_account_aws_id_format():
    account = make_account()
    assert account.id.startswith("aws:")
    assert account.provider_id == "123456789012"


def test_cloud_account_tags_default_empty():
    account = make_account()
    assert account.tags == {}


def test_cloud_account_azure_id_format():
    account = make_account(
        id="azure:aaaabbbb-1234-5678-abcd-000011112222",
        provider_id="aaaabbbb-1234-5678-abcd-000011112222",
        provider="azure",
        name="my-subscription",
    )
    assert account.provider == "azure"
    assert account.id.startswith("azure:")


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------

def test_team_id_format():
    team = Team(
        id="team:platform-engineering",
        provider_id="platform-engineering",
        source_adapter="static_yaml",
        collected_at=NOW,
        display_name="Platform Engineering",
    )
    assert team.id == "team:platform-engineering"
    assert team.provider_id == "platform-engineering"


def test_team_members_default_empty():
    team = Team(
        id="team:data-infra",
        provider_id="data-infra",
        source_adapter="static_yaml",
        collected_at=NOW,
        display_name="Data Infra",
    )
    assert team.members == []


# ---------------------------------------------------------------------------
# DeploymentMapping
# ---------------------------------------------------------------------------

def test_deployment_mapping_stable_ids_in_composite_key():
    mapping = DeploymentMapping(
        id="github:12345678::aws:123456789012::prod",
        provider_id="github:12345678::aws:123456789012::prod",
        source_adapter="github",
        collected_at=NOW,
        repo_id="github:12345678",
        target_type="cloud_account",
        target_id="aws:123456789012",
        deploy_method="github_actions_oidc",
        environment="prod",
        detection_method="oidc_workflow",
    )
    assert "github:12345678" in mapping.id
    assert "aws:123456789012" in mapping.id
    assert mapping.detection_method == "oidc_workflow"


def test_deployment_mapping_target_id_optional_for_variable_oidc():
    """When OIDC role ARN contains a variable, target_id may be None."""
    mapping = DeploymentMapping(
        id="github:12345678::unknown::any",
        provider_id="github:12345678::unknown::any",
        source_adapter="github",
        collected_at=NOW,
        repo_id="github:12345678",
        target_type="cloud_account",
        target_id=None,
        detection_method="oidc_workflow",
        notes="role-to-assume: ${{ vars.DEPLOY_ROLE_ARN }}",
    )
    assert mapping.target_id is None
    assert mapping.notes is not None


# ---------------------------------------------------------------------------
# GhasAlert
# ---------------------------------------------------------------------------

def test_ghas_alert_id_contains_repo_stable_id():
    alert = GhasAlert(
        id="github:12345678::alert::secret_scanning::42",
        provider_id="42",
        source_adapter="github",
        collected_at=NOW,
        repo_id="github:12345678",
        alert_type="secret_scanning",
        number=42,
        state="open",
        url="https://github.com/my-org/my-repo/security/secret-scanning/42",
    )
    assert alert.repo_id == "github:12345678"
    assert "github:12345678" in alert.id
    assert alert.state == "open"


def test_ghas_alert_severity_optional():
    alert = GhasAlert(
        id="github:12345678::alert::dependabot::1",
        provider_id="1",
        source_adapter="github",
        collected_at=NOW,
        repo_id="github:12345678",
        alert_type="dependabot",
        number=1,
        state="open",
        url="https://github.com/my-org/my-repo/security/dependabot/1",
    )
    assert alert.severity is None
