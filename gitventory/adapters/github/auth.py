"""GitHub authentication configuration types.

Three modes are supported, selected via the ``type`` discriminator field:

  app           GitHub App (recommended for enterprise)
                One App registration covers all orgs. Each org gets a separate
                short-lived installation token (1 h TTL) generated at runtime.

  token_per_org One PAT per organisation.
                Better blast-radius isolation than a global PAT, but tokens are
                still user-bound and require manual rotation.

  token         Single global PAT (simple / local / single-org setups only).
                Not recommended when scanning multiple organisations.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


class AppAuthConfig(BaseModel):
    """GitHub App authentication.

    Register a GitHub App once, then install it into each target organisation.
    gitventory uses the single ``app_id`` + private key to authenticate as the
    App, then generates a per-org installation token scoped only to that org.

    Private key precedence: ``private_key`` (inline) > ``private_key_file`` (path).
    """

    type: Literal["app"] = "app"

    app_id: int
    """Numeric App ID shown on the GitHub App settings page."""

    private_key_file: Optional[str] = None
    """Path to the App's private key PEM file downloaded from GitHub."""

    private_key: Optional[str] = None
    """Inline PEM content — useful when injecting from a secrets manager or env var.
    Takes precedence over ``private_key_file`` if both are set."""

    installation_ids: dict[str, int] = {}
    """Optional map of ``org_name → installation_id``.
    If an org is absent, gitventory auto-discovers its installation via the API.
    Pinning avoids one extra API call per org and is useful when the App is
    installed in many organisations."""

    @model_validator(mode="after")
    def require_key(self) -> "AppAuthConfig":
        if not self.private_key and not self.private_key_file:
            raise ValueError(
                "GitHub App auth requires either 'private_key' (inline PEM) or "
                "'private_key_file' (path to .pem file)."
            )
        return self

    def resolve_private_key(self) -> str:
        """Return the PEM string, reading the file if necessary."""
        if self.private_key:
            return self.private_key
        from pathlib import Path
        path = Path(self.private_key_file)  # type: ignore[arg-type]
        if not path.exists():
            raise FileNotFoundError(
                f"GitHub App private key file not found: {self.private_key_file}"
            )
        return path.read_text(encoding="utf-8")


class TokenPerOrgConfig(BaseModel):
    """Per-organisation PAT authentication.

    Each org maps to its own token.  The token for the org being scanned is
    selected at runtime — a leaked token only affects a single organisation.
    """

    type: Literal["token_per_org"] = "token_per_org"

    org_tokens: dict[str, str] = {}
    """Map of ``org_name → personal_access_token``."""

    def token_for(self, org: str) -> str:
        token = self.org_tokens.get(org, "")
        if not token:
            raise KeyError(
                f"No token configured for org {org!r}. "
                f"Add it under adapters.github.auth.org_tokens.{org} in config.yaml."
            )
        return token


class TokenAuthConfig(BaseModel):
    """Single global PAT — suitable for simple / single-org / local setups."""

    type: Literal["token"] = "token"

    token: str = ""
    """Personal access token.  Falls back to the ``GITHUB_TOKEN`` environment
    variable if not set explicitly."""

    @model_validator(mode="after")
    def fill_from_env(self) -> "TokenAuthConfig":
        if not self.token:
            import os
            self.token = os.environ.get("GITHUB_TOKEN", "")
        return self


# ---------------------------------------------------------------------------
# Discriminated union — Pydantic v2 selects the right class via `type`
# ---------------------------------------------------------------------------

GitHubAuth = Annotated[
    Union[AppAuthConfig, TokenPerOrgConfig, TokenAuthConfig],
    Field(discriminator="type"),
]
