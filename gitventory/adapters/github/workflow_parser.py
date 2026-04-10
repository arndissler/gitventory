"""OIDC workflow parser — detect repo→AWS account links from GitHub Actions workflow files.

Strategy:
  For each repo, fetch .github/workflows/*.yml (and .yaml).
  Walk the jobs[*].steps tree looking for:
    uses: aws-actions/configure-aws-credentials@*
  Extract `with.role-to-assume`.
  If the value is a literal ARN, extract the account ID from field 4.
  If the value contains a template expression (${{ ... }}), record a partial mapping.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterator, Optional

import yaml
from github.Repository import Repository as GHRepository

from gitventory.models.deployment_mapping import DeploymentMapping

logger = logging.getLogger(__name__)

# Matches aws-actions/configure-aws-credentials in any version pin form
_OIDC_ACTION_RE = re.compile(
    r"aws-actions/configure-aws-credentials(@[^@\s]+)?", re.IGNORECASE
)

# Matches a literal role ARN: arn:aws:iam::ACCOUNT_ID:role/ROLE_NAME
# The account ID is capture group 1.
_ROLE_ARN_RE = re.compile(
    r"arn:aws:iam::(\d{12}):role/([^\s\"']+)"
)

# Matches template expressions like ${{ vars.ROLE_ARN }} or ${{ secrets.ROLE }}
_TEMPLATE_EXPR_RE = re.compile(r"\$\{\{.*?\}\}")


def parse_workflows(
    repo: GHRepository,
    repo_entity_id: str,
    collected_at: datetime,
    client,  # GitHubClient — passed to avoid circular imports
) -> Iterator[DeploymentMapping]:
    """
    Parse all workflow files in a repo and yield DeploymentMapping entities
    for every OIDC role assumption found.
    """
    workflows_dir = client.get_repo_contents(repo, ".github/workflows")
    if not workflows_dir:
        return

    # get_repo_contents returns a list when path is a directory
    if not isinstance(workflows_dir, list):
        workflows_dir = [workflows_dir]

    for content_file in workflows_dir:
        name: str = content_file.name
        if not (name.endswith(".yml") or name.endswith(".yaml")):
            continue

        raw_text = client.get_file_content(repo, content_file.path)
        if not raw_text:
            continue

        try:
            yield from _parse_single_workflow(
                raw_text,
                workflow_path=content_file.path,
                repo_entity_id=repo_entity_id,
                collected_at=collected_at,
            )
        except Exception as exc:
            logger.debug(
                "Could not parse workflow %s in %s: %s",
                content_file.path, repo.full_name, exc,
            )


def _parse_single_workflow(
    raw_text: str,
    workflow_path: str,
    repo_entity_id: str,
    collected_at: datetime,
) -> Iterator[DeploymentMapping]:
    """Parse a single workflow YAML and yield DeploymentMapping entities."""
    try:
        workflow = yaml.safe_load(raw_text)
    except yaml.YAMLError:
        return

    if not isinstance(workflow, dict):
        return

    jobs = workflow.get("jobs") or {}
    if not isinstance(jobs, dict):
        return

    seen: set[str] = set()  # deduplicate within this file

    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps") or []
        if not isinstance(steps, list):
            continue

        for step in steps:
            if not isinstance(step, dict):
                continue

            uses: str = step.get("uses", "") or ""
            if not _OIDC_ACTION_RE.search(uses):
                continue

            # Found aws-actions/configure-aws-credentials
            with_block = step.get("with") or {}
            role_value: Optional[str] = (
                with_block.get("role-to-assume")
                or with_block.get("role_to_assume")  # Some people use underscores
            )
            if not role_value:
                continue

            role_value = str(role_value).strip()

            # Attempt literal ARN parse
            arn_match = _ROLE_ARN_RE.search(role_value)
            if arn_match:
                account_id = arn_match.group(1)
                role_name = arn_match.group(2)
                target_id = f"aws:{account_id}"
                dedup_key = f"{target_id}::oidc"

                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                mapping_id = f"{repo_entity_id}::{target_id}::oidc"
                yield DeploymentMapping(
                    id=mapping_id,
                    provider_id=mapping_id,
                    source_adapter="github",
                    collected_at=collected_at,
                    repo_id=repo_entity_id,
                    target_type="cloud_account",
                    target_id=target_id,
                    deploy_method="github_actions_oidc",
                    environment=None,  # Not determinable from workflow alone
                    detection_method="oidc_workflow",
                    notes=(
                        f"Role: arn:aws:iam::{account_id}:role/{role_name}, "
                        f"Workflow: {workflow_path}, Job: {job_name}"
                    ),
                )
                logger.debug(
                    "OIDC mapping detected: %s → aws:%s (role: %s, workflow: %s)",
                    repo_entity_id, account_id, role_name, workflow_path,
                )

            elif _TEMPLATE_EXPR_RE.search(role_value):
                # Template expression — account ID is indeterminate
                dedup_key = f"variable::{role_value}"
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                mapping_id = f"{repo_entity_id}::aws:unknown::oidc_variable"
                yield DeploymentMapping(
                    id=mapping_id,
                    provider_id=mapping_id,
                    source_adapter="github",
                    collected_at=collected_at,
                    repo_id=repo_entity_id,
                    target_type="cloud_account",
                    target_id=None,  # Unknown — role ARN uses a template variable
                    deploy_method="github_actions_oidc",
                    environment=None,
                    detection_method="oidc_workflow",
                    notes=(
                        f"role-to-assume uses variable: {role_value}, "
                        f"Workflow: {workflow_path}, Job: {job_name}"
                    ),
                )
                logger.debug(
                    "Partial OIDC mapping (variable ARN): %s, workflow: %s",
                    repo_entity_id, workflow_path,
                )
