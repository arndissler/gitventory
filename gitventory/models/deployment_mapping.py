"""DeploymentMapping entity — explicit many-to-many link between a repo and a cloud target."""

from __future__ import annotations

from typing import Optional

from gitventory.models.base import InventoryEntity


class DeploymentMapping(InventoryEntity):
    """
    A reified relationship: one repository deploys to one cloud target
    (account, subscription, or future k8s cluster) in one environment.

    ``id`` format: ``"{repo_id}::{target_id}::{environment or 'any'}"``

    Both ``repo_id`` and ``target_id`` are stable internal IDs (``github:NNN``,
    ``aws:NNN``, etc.) — they survive renames on either side.

    ``detection_method`` records how the mapping was established:
      - ``"oidc_workflow"``  — parsed from a GitHub Actions workflow file
      - ``"static_yaml"``   — manually declared in deployment_mappings.yaml
      - ``"aws_orgs_tag"``  — future: derived from AWS resource tags
    """

    repo_id: str
    """FK → Repository.id (stable ``github:NNN`` or ``azuredevops:<uuid>``)."""

    target_type: str
    """``"cloud_account"`` | ``"k8s_cluster"`` | …"""

    target_id: Optional[str] = None
    """FK → CloudAccount.id (stable ``aws:NNN`` etc.).
    None when the account could not be resolved (e.g. OIDC role ARN uses a variable)."""

    deploy_method: Optional[str] = None
    """``"github_actions_oidc"`` | ``"helm"`` | ``"terraform"`` | ``"codedeploy"`` | …"""

    environment: Optional[str] = None
    """``"prod"`` | ``"staging"`` | ``"dev"`` | …  None means indeterminate/any."""

    detection_method: str = "static_yaml"
    """How this mapping was established. See class docstring."""

    notes: Optional[str] = None
    """Free-text context, e.g. the role ARN and workflow file path for OIDC mappings."""
