# gitventory

A modular inventory service that links source-code repositories to their deployed cloud environments, tracks security posture, and clarifies ownership — across hundreds of repositories and accounts.

## The problem

When you manage hundreds of GitHub repositories across dozens of AWS accounts and multiple teams, a set of questions becomes surprisingly hard to answer:

- Which service runs in AWS account `123456789012`? Which repositories deploy to it?
- Which repositories haven't been touched in six months but still have open secret-scanning alerts?
- Who is responsible for this repository — is it a proof-of-concept or a production IDP?
- We have an active CVE in a dependency. Which services are actually affected?

The connection between a repository and its deployed application is typically undocumented. gitventory makes it visible.

## How it works

gitventory **collects metadata** from multiple sources via pluggable adapters and stores it in a local SQLite database. You query that database from the CLI or export it as JSON.

```
config.yaml
    │
    ├─► GitHubAdapter          Repos, GHAS alerts, OIDC workflow parser
    ├─► StaticYamlAdapter      Teams, AWS accounts, manual deployment mappings
    └─► (future adapters)      AWS Organizations, Azure, Azure DevOps, Kubernetes
              │
              ▼
        SQLite store  ──►  CLI queries  /  JSON export  /  (future: Web UI)
```

### OIDC auto-detection

The primary strategy for linking a repository to an AWS account is parsing GitHub Actions workflow files. When a workflow uses `aws-actions/configure-aws-credentials` with an IAM role ARN, gitventory extracts the account ID from the ARN automatically — no manual mapping file needed.

```yaml
# .github/workflows/deploy.yml  (in your repository)
- uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: arn:aws:iam::123456789012:role/deploy-role
    aws-region: eu-central-1
```

This produces a `DeploymentMapping` with `detection_method: oidc_workflow` — the cleanest evidence available that this repository deploys to that account.

For repositories that use other deployment methods, a fallback `inventory/deployment_mappings.yaml` file accepts manually declared mappings.

### Stable identifiers

Every entity has a **provider-namespaced stable ID** that survives renames and transfers:

| Entity | Stable ID format | Example |
|---|---|---|
| GitHub repository | `github:{numeric_repo_id}` | `github:12345678` |
| AWS account | `aws:{account_id}` | `aws:123456789012` |
| Azure subscription | `azure:{subscription_uuid}` | `azure:aaaa-bbbb-...` |
| Team | `team:{slug}` | `team:platform-engineering` |

If you rename `my-org/old-name` to `my-org/new-name`, all deployment mappings and alerts remain linked. The `full_name` field is a mutable display label updated on every collect run.

---

## Installation

Requires Python 3.11+.

```bash
git clone https://github.com/your-org/gitventory
cd gitventory

python -m venv .venv
# Windows:
.venv\Scripts\pip install -e .
# macOS / Linux:
.venv/bin/pip install -e .
```

Copy the example config and fill in your values:

```bash
cp config.example.yaml config.yaml
```

---

## Configuration

`config.yaml` drives everything. Environment variables are interpolated with `${VAR_NAME}` syntax (supports `${VAR:-default}` fallbacks).

```yaml
version: "1"

store:
  backend: sqlite
  sqlite:
    path: "./data/gitventory.db"

adapters:
  github:
    enabled: true
    auth:
      type: app                                        # recommended for enterprise
      app_id: "${GITHUB_APP_ID}"
      private_key_file: "${GITHUB_APP_PRIVATE_KEY_FILE}"
    orgs:
      - my-org
    collect_ghas_alerts: true
    parse_workflows: true                              # auto-detect OIDC repo→AWS links

  static_yaml:
    enabled: true
    teams_file: "./inventory/teams.yaml"
    aws_accounts_file: "./inventory/aws_accounts.yaml"
    deployment_mappings_file: "./inventory/deployment_mappings.yaml"
```

See [`config.example.yaml`](config.example.yaml) for the full reference including all three auth modes.

### GitHub authentication modes

gitventory supports three auth modes for the GitHub adapter, selected via `auth.type`:

| Mode | `auth.type` | Recommended for |
|---|---|---|
| **GitHub App** | `app` | Enterprise — multiple orgs, no user dependency |
| Per-org PATs | `token_per_org` | Teams without GitHub App access |
| Global PAT | `token` | Local use, single-org setups |

#### Setting up a GitHub App (recommended)

1. Go to **Settings → Developer settings → GitHub Apps → New GitHub App**
   (or your organisation's settings for an org-owned App)
2. Set a name, disable the webhook, and grant these **repository permissions**:
   - Contents: Read
   - Metadata: Read
   - Security events: Read (for GHAS alerts)
3. Save and note the **App ID** shown at the top of the App settings page
4. Scroll to **Private keys** and click **Generate a private key** — save the `.pem` file
5. Install the App into each target organisation (**Install App** tab → select orgs)
6. Set environment variables:
   ```bash
   export GITHUB_APP_ID=123456
   export GITHUB_APP_PRIVATE_KEY_FILE=/path/to/private-key.pem
   ```

gitventory auto-discovers the installation ID for each org in `orgs:`. Optionally pin them under `auth.installation_ids` to skip the discovery API call.

#### Setting up per-org PATs (`token_per_org`)

Use **fine-grained personal access tokens** (not classic PATs) — they can be scoped to a single organisation and specific repositories.

Create one token per org at **Settings → Developer settings → Personal access tokens → Fine-grained tokens**:

| Permission | Level | Required for |
|---|---|---|
| **Contents** | Read | Listing repos, reading workflow files (OIDC detection) |
| **Metadata** | Read | Repo metadata — always required by the API |
| **Secret scanning alerts** | Read | `collect_secret_scanning: true` |
| **Code scanning alerts** | Read | `collect_ghas_alerts: true` |

Dependabot alerts use the **Dependabot alerts** permission (Read).  
If you only need repo metadata and OIDC mapping detection, Contents + Metadata is sufficient.

Set **Resource owner** to the target organisation and restrict **Repository access** to the repositories gitventory needs to scan, or select _All repositories_ for full org coverage.

#### Setting up a global PAT (`token`)

Same permissions as per-org PATs above. If using a classic PAT, the required OAuth scopes are:

| Scope | Required for |
|---|---|
| `repo` | Private repo access (read) |
| `read:org` | Listing org repositories |
| `security_events` | GHAS alerts (secret scanning, code scanning, Dependabot) |

Classic PATs cannot be scoped to a single org — a leaked token grants access to everything the owning user can reach. Prefer fine-grained PATs or GitHub Apps.

### Static inventory files

These live in `inventory/` and are maintained by hand. They complement what can be auto-detected.

**`inventory/teams.yaml`** — ownership anchors:
```yaml
teams:
  - id: platform-engineering      # stable slug — used as foreign key everywhere
    display_name: Platform Engineering
    email: platform@example.com
    slack_channel: "#platform-eng"
    github_team_slug: platform-engineering
```

**`inventory/aws_accounts.yaml`** — known AWS accounts:
```yaml
accounts:
  - id: "123456789012"
    name: prod-platform
    environment: prod
    ou_path: /root/workloads/prod
    owning_team: platform-engineering
    tags:
      cost-center: "1234"
```

**`inventory/deployment_mappings.yaml`** — fallback for repos without OIDC:
```yaml
mappings:
  - repo: my-org/legacy-service
    target_type: cloud_account
    target_id: "aws:123456789012"
    deploy_method: codedeploy
    environment: prod
```

**`inventory/catalog.yaml`** — organizational meta-model (see [catalog section](#catalog) below):
```yaml
catalog:
  entity_types:
    - id: service
      display_name: Service
  entities:
    - id: checkout-api
      type: service
      display_name: Checkout API
      owning_team: platform-engineering
      properties:
        criticality: critical
      matchers:
        repos:
          - full_name: "my-org/checkout-api"
        accounts:
          - id: "aws:123456789012"
```

---

## Catalog

The catalog is gitventory's organizational meta-model. It lets you define the entities that people in your company actually talk about — services, projects, domains, or whatever taxonomy fits your organization — and link them to technical artifacts (repositories, cloud accounts) using declarative **matchers**.

No files need to be added to any repository. Matching is configured centrally in `inventory/catalog.yaml`.

### Entity types

Entity types are fully user-defined. There is no fixed hierarchy. Define the types that make sense:

```yaml
catalog:
  entity_types:
    - id: service
      display_name: Service
    - id: project
      display_name: Project
    - id: domain
      display_name: Business Domain
```

### Matchers

Matchers link catalog entities to repos and cloud accounts. Rules within a single entity are OR'd — any matching rule creates a link. One artifact can belong to multiple entities.

**Repo matchers:**

| Matcher | Example | Description |
|---|---|---|
| `full_name` | `"my-org/checkout-api"` | Exact match |
| `full_name` | `"my-org/checkout-*"` | Glob pattern (`fnmatch`) |
| `topics.any` | `[checkout, payments]` | Repo has any of these topics |
| `github_property` | `{name: service, value: checkout}` | GitHub custom property |

**Account matchers:**

| Matcher | Example | Description |
|---|---|---|
| `id` | `"aws:123456789012"` | Stable account ID |
| `tags` | `{service: checkout}` | Account has all these tags |
| `environment` | `prod` | Direct field equality |
| `provider` | `aws` | Direct field equality |

### Criticality and weighted alert priority

Set `criticality` in `properties` to weight alerts from that entity's repos:

```yaml
properties:
  criticality: critical   # critical | high | medium | low
```

When querying alerts with `--sort-by weighted-priority`, the original severity is preserved but a `weighted_priority` score is computed: `severity_score × criticality_weight`. A `high` alert in a `critical` service scores higher than the same alert in a `low`-criticality tool.

| Criticality | Weight |
|---|---|
| `critical` | 2.0 |
| `high` | 1.5 |
| `medium` | 1.0 |
| `low` | 0.5 |
| *(unlinked)* | 1.0 |

### Catalog sync

Catalog matching runs automatically after `gitventory collect`. You can also run it independently:

```bash
gitventory catalog sync              # evaluate matchers, update links
gitventory catalog sync --clear      # wipe all links first, then rebuild
gitventory catalog sync --clear -v   # verbose output
```

---

## Usage

### Collect

```bash
# Collect from all enabled adapters
gitventory collect

# Collect from a specific adapter only
gitventory collect -a static_yaml
gitventory collect -a github

# Collect a single repository — fetch only that repo, its GHAS alerts,
# and any OIDC workflow mappings, then upsert into the store
gitventory collect --repo my-org/my-repo
gitventory collect --repo my-org/my-repo --dry-run -v

# Dry run — show what would be collected without writing to the store
gitventory collect --dry-run -v
```

### Query repositories

```bash
# All repositories (table view)
gitventory query repos

# A single repository by full name or stable ID
gitventory query repos --repo my-org/my-repo
gitventory query repos --repo github:12345678 -o json

# Repos not pushed to in 90+ days with open GHAS alerts
gitventory query repos --stale-days 90 --has-alerts

# Repos owned by a specific team
gitventory query repos --team platform-engineering

# Repos in a specific GitHub org, non-archived, as JSON
gitventory query repos --org my-org --no-archived -o json

# Generic filter: repos with >0 secret alerts
gitventory query repos -f "open_secret_alerts>0"
```

> **Tip:** `gitventory show repo my-org/my-repo` shows the full detail view for a single repository (all fields, no column filtering). `query repos --repo` returns the same condensed table row as a regular list query, which is easier to pipe or export as JSON.

### Query catalog entities

```bash
# All catalog entities
gitventory query catalog

# Filter by entity type
gitventory query catalog --type service
gitventory query catalog --type project -o json

# Filter by criticality
gitventory query catalog --criticality critical

# Repos linked to a specific catalog entity
gitventory query repos --catalog-entity checkout-api
gitventory query repos --catalog-entity catalog:service:checkout-api

# Show full detail for a catalog entity (properties + linked repos and accounts)
gitventory show catalog checkout-api
gitventory show catalog service:checkout-api
```

### Query cloud accounts

```bash
gitventory query accounts
gitventory query accounts --provider aws --env prod
gitventory query accounts --team platform-engineering -o json
```

### Query deployment mappings

```bash
# All mappings
gitventory query mappings

# What deploys to this AWS account?
gitventory query mappings --account aws:123456789012

# Only OIDC-detected mappings
gitventory query mappings --method oidc_workflow

# What does this repo deploy to?
gitventory query mappings --repo github:12345678
# or by slug (resolved via full_name):
gitventory query mappings --repo my-org/my-service
```

### Query GHAS alerts

```bash
# All open secret scanning alerts
gitventory query alerts --type secret_scanning

# High-severity Dependabot alerts
gitventory query alerts --type dependabot --severity high

# All alerts for a specific repo
gitventory query alerts --repo github:12345678

# All alerts for repos linked to a catalog entity
gitventory query alerts --catalog-entity checkout-api

# Sorted by weighted priority (severity × service criticality weight)
gitventory query alerts --sort-by weighted-priority
gitventory query alerts --catalog-entity checkout-api --sort-by weighted-priority -o json
```

### Inspect a single entity

```bash
gitventory show repo github:12345678
gitventory show repo my-org/my-repo       # resolved by full_name
gitventory show account aws:123456789012
gitventory show account 123456789012      # bare account ID also accepted
gitventory show team platform-engineering
```

### Store management

```bash
# Show entity counts and last collection times
gitventory store status

# Export everything to JSON (for dashboards, scripts, spreadsheets)
gitventory store export ./exports/snapshot-$(date +%Y-%m-%d).json

# Initialise schema (done automatically on first use)
gitventory store init
```

### Adapter management

```bash
# List all registered adapters and their enabled status
gitventory adapters list
```

---

## Data model

```
Team ──< CloudAccount       (owning_team_id)
Team ──< Repository         (owning_team_id)
Repository ──< GhasAlert    (repo_id  →  stable github:NNN)
Repository ──< DeploymentMapping
DeploymentMapping >── CloudAccount
```

| Entity | Key fields |
|---|---|
| `Repository` | `provider`, `full_name` (mutable), `language`, `topics`, `visibility`, `is_archived`, `last_push_at`, GHAS alert counts, `owning_team_id` |
| `CloudAccount` | `provider` (aws/azure), `name`, `environment`, `ou_path`, `owning_team_id`, `tags` |
| `Team` | `display_name`, `email`, `slack_channel`, `github_team_slug` |
| `DeploymentMapping` | `repo_id`, `target_id`, `deploy_method`, `environment`, `detection_method` |
| `GhasAlert` | `alert_type`, `state`, `severity`, `secret_type`, `rule_id` |

---

## Adding a new adapter

1. Create `gitventory/adapters/your_adapter/adapter.py` with a class decorated `@register_adapter`
2. Implement `ADAPTER_NAME`, `CONFIG_CLASS`, and `collect() -> Iterator[InventoryEntity]`
3. Import it in `gitventory/adapters/your_adapter/__init__.py`
4. Import the subpackage in `gitventory/adapters/__init__.py`
5. Add its config schema to `AdaptersConfig` in `gitventory/config.py`

```python
from gitventory.adapters.base import AbstractAdapter, AdapterConfig
from gitventory.registry import register_adapter

class MyAdapterConfig(AdapterConfig):
    api_url: str
    token: str

@register_adapter
class MyAdapter(AbstractAdapter):
    ADAPTER_NAME = "my_adapter"
    CONFIG_CLASS = MyAdapterConfig

    def collect(self):
        # yield InventoryEntity instances
        ...
```

Adapters never interact with the store directly — they only yield entities.

---

## Development

```bash
# Run all tests
.venv/Scripts/pytest tests/ -v           # Windows
.venv/bin/pytest tests/ -v               # macOS / Linux

# Run only unit tests (no filesystem/DB)
pytest tests/unit/

# Run only integration tests
pytest tests/integration/
```

### Planned adapters

| Adapter | Status | Description |
|---|---|---|
| `github` | Done | Repos, GHAS alerts, OIDC workflow parser |
| `static_yaml` | Done | Teams, AWS accounts, manual mappings |
| `aws_orgs` | Planned | AWS Organizations — OUs, account IDs, tags |
| `azuredevops` | Planned | Azure DevOps repositories |
| `azure` | Planned | Azure subscriptions and resource groups |
| `kubernetes` | Planned | Kubernetes clusters and Helm releases |

### Storage backends

| Backend | Status |
|---|---|
| SQLite | Done (default) |
| JSON files | Done (dev/test) |
| PostgreSQL | Planned (implement `PostgresStore(AbstractStore)`, change one config line) |

---

## License

MIT
