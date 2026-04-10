"""StaticYamlAdapter — loads teams, AWS accounts, and deployment mappings from YAML files."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import yaml
from pydantic import BaseModel

from gitventory.adapters.base import AbstractAdapter, AdapterConfig
from gitventory.adapters.static_yaml.schema import (
    AwsAccountsFile,
    DeploymentMappingsFile,
    TeamsFile,
)
from gitventory.models.cloud_account import CloudAccount
from gitventory.models.deployment_mapping import DeploymentMapping
from gitventory.models.base import InventoryEntity
from gitventory.models.team import Team
from gitventory.registry import register_adapter

logger = logging.getLogger(__name__)


class StaticYamlAdapterConfig(AdapterConfig):
    teams_file: Optional[str] = None
    aws_accounts_file: Optional[str] = None
    deployment_mappings_file: Optional[str] = None


@register_adapter
class StaticYamlAdapter(AbstractAdapter):
    """Reads human-maintained YAML files and yields inventory entities."""

    ADAPTER_NAME = "static_yaml"
    CONFIG_CLASS = StaticYamlAdapterConfig

    def __init__(self, config: StaticYamlAdapterConfig) -> None:
        super().__init__(config)
        self._collected_at = datetime.now(timezone.utc)

    def collect(self) -> Iterator[InventoryEntity]:
        cfg: StaticYamlAdapterConfig = self.config  # type: ignore[assignment]

        if cfg.teams_file:
            yield from self._load_teams(cfg.teams_file)
        if cfg.aws_accounts_file:
            yield from self._load_aws_accounts(cfg.aws_accounts_file)
        if cfg.deployment_mappings_file:
            yield from self._load_deployment_mappings(cfg.deployment_mappings_file)

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------

    def _load_teams(self, path: str) -> Iterator[Team]:
        data = self._read_yaml(path)
        file = TeamsFile(**data)
        for entry in file.teams:
            yield Team(
                id=f"team:{entry.id}",
                provider_id=entry.id,
                source_adapter=self.ADAPTER_NAME,
                collected_at=self._collected_at,
                display_name=entry.display_name,
                email=entry.email,
                slack_channel=entry.slack_channel,
                github_team_slug=entry.github_team_slug,
                members=entry.members,
                raw=entry.model_dump(),
            )
            logger.debug("Loaded team: team:%s", entry.id)

    # ------------------------------------------------------------------
    # AWS accounts
    # ------------------------------------------------------------------

    def _load_aws_accounts(self, path: str) -> Iterator[CloudAccount]:
        data = self._read_yaml(path)
        file = AwsAccountsFile(**data)
        for entry in file.accounts:
            owning_team_id = (
                f"team:{entry.owning_team}" if entry.owning_team else None
            )
            yield CloudAccount(
                id=f"aws:{entry.id}",
                provider_id=entry.id,
                provider="aws",
                source_adapter=self.ADAPTER_NAME,
                collected_at=self._collected_at,
                name=entry.name,
                environment=entry.environment,
                ou_path=entry.ou_path,
                owning_team_id=owning_team_id,
                tags=entry.tags,
                raw=entry.model_dump(),
            )
            logger.debug("Loaded AWS account: aws:%s (%s)", entry.id, entry.name)

    # ------------------------------------------------------------------
    # Deployment mappings
    # ------------------------------------------------------------------

    def _load_deployment_mappings(self, path: str) -> Iterator[DeploymentMapping]:
        data = self._read_yaml(path)
        file = DeploymentMappingsFile(**data)
        for entry in file.mappings:
            # repo is a full_name slug at this point; stable ID resolution happens
            # in the runner after the GitHub adapter has run.  We store the slug
            # in repo_id and mark it for later resolution.
            env = entry.environment or "any"
            target_id = entry.target_id

            mapping_id = f"static::{entry.repo}::{target_id}::{env}"
            yield DeploymentMapping(
                id=mapping_id,
                provider_id=mapping_id,
                source_adapter=self.ADAPTER_NAME,
                collected_at=self._collected_at,
                repo_id=entry.repo,          # slug — resolved later
                target_type=entry.target_type,
                target_id=target_id,
                deploy_method=entry.deploy_method,
                environment=entry.environment,
                detection_method="static_yaml",
                notes=entry.notes,
                raw=entry.model_dump(),
            )
            logger.debug(
                "Loaded deployment mapping: %s → %s (%s)",
                entry.repo, target_id, entry.environment,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_yaml(path: str) -> dict:
        p = Path(path)
        if not p.exists():
            logger.warning("Static YAML file not found, skipping: %s", path)
            return {}
        with p.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
