# AgenticOrg C3 Hosted Commerce Smoke Runbook

Status: C3-prep only. This runbook does not deploy, create cloud resources, change production config, enable production Commerce V1, enable live payments, or enable live Plural.

## Goal

C3 covers the currently skipped hosted gap after C2G: AgenticOrg MCP/A2A discovery and hosted real-staging runtime behavior against a temporary Grantex Option A smoke service.

Run C3 hosted smoke after C2G smoke evidence is complete and merged, unless an explicit approval says otherwise.

The cheapest sufficient topology is an API-only temporary AgenticOrg Cloud Run smoke service. UI, worker, and beat are deferred because they do not add coverage for MCP discovery, A2A discovery, or the Commerce Sales Agent Grantex-only runtime path.

## Recommended Topology

Use only these temporary AgenticOrg resources:

| Resource | Name |
| --- | --- |
| Cloud Run API service | `agenticorg-api-commerce-smoke` |
| Optional Cloud Run eval job | `agenticorg-commerce-smoke-eval` |
| Optional migration job | `agenticorg-commerce-smoke-migrate` |
| Cloud SQL instance | `agenticorg-commerce-smoke-pg` |
| Redis instance | `agenticorg-commerce-smoke-redis` |
| API image tag | `agenticorg-api:commerce-smoke-<sha>` |

The Grantex side remains the temporary Option A smoke topology:

| Resource | Name |
| --- | --- |
| Cloud Run service | `grantex-auth-smoke` |
| Cloud SQL instance | `grantex-commerce-smoke-pg` |
| Redis instance | `grantex-commerce-smoke-redis` |

## Non-Secret Env Vars

Set these on the temporary AgenticOrg API service and optional eval job only:

```text
AGENTICORG_ENV=staging
AGENTICORG_BASE_URL=<agenticorg-smoke-cloud-run-origin>
AGENTICORG_PUBLIC_API_BASE_URL=<agenticorg-smoke-cloud-run-origin>
AGENTICORG_CORS_ALLOWED_ORIGINS=<agenticorg-smoke-cloud-run-origin>
AGENTICORG_GIT_SHA=<agenticorg-main-sha>
AGENTICORG_ENABLE_LEGACY_STARTUP_DDL=0
AGENTICORG_COMMERCE_REAL_STAGING=1
GRANTEX_COMMERCE_BASE_URL=<grantex-smoke-cloud-run-origin>
GRANTEX_BASE_URL=<grantex-smoke-cloud-run-origin>
AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL=<grantex-smoke-cloud-run-origin>
COMMERCE_LIVE_MODE_ENABLED=0
PLURAL_LIVE_ENABLED=0
PLURAL_ENV=sandbox
```

## Required Secrets By Name Only

Use smoke-only secret names. Do not copy production secret versions.

```text
agenticorg-commerce-smoke-secret-key
agenticorg-commerce-smoke-db-url
agenticorg-commerce-smoke-redis-url
agenticorg-commerce-smoke-grantex-api-key
agenticorg-commerce-smoke-fixture-env
```

Use exactly one Grantex auth source name in the runner:

```text
GRANTEX_COMMERCE_BEARER_TOKEN
GRANTEX_AGENT_ASSERTION
GRANTEX_API_KEY
```

Do not write auth values, passports, idempotency values, DB/Redis URLs, private keys, or provider material into docs, logs, evidence, PRs, or chat.

## Command Plan, Not Run

Build image:

```powershell
gcloud builds submit --config=cloudbuild-api.yaml --substitutions=_IMAGE=<registry>/agenticorg-api:commerce-smoke-<sha>
```

Create temporary AgenticOrg smoke resources:

```powershell
gcloud sql instances create agenticorg-commerce-smoke-pg --tier=db-f1-micro --region=us-central1 --deletion-protection=false
gcloud redis instances create agenticorg-commerce-smoke-redis --region=us-central1 --size=1 --tier=basic
```

Create smoke-only secrets by name only, with values supplied outside logs:

```powershell
gcloud secrets create agenticorg-commerce-smoke-secret-key
gcloud secrets create agenticorg-commerce-smoke-db-url
gcloud secrets create agenticorg-commerce-smoke-redis-url
gcloud secrets create agenticorg-commerce-smoke-grantex-api-key
gcloud secrets create agenticorg-commerce-smoke-fixture-env
```

Run migrations once if the temp database requires them:

```powershell
gcloud run jobs create agenticorg-commerce-smoke-migrate --image=<image> --command=python --args=scripts/alembic_migrate.py
gcloud run jobs execute agenticorg-commerce-smoke-migrate --wait
```

Deploy the temporary API-only smoke service with min instances 0 and max instances 1:

```powershell
gcloud run deploy agenticorg-api-commerce-smoke --image=<image> --region=us-central1 --min-instances=0 --max-instances=1
gcloud run services update agenticorg-api-commerce-smoke --region=us-central1 --update-env-vars=AGENTICORG_BASE_URL=<agenticorg-smoke-url>,AGENTICORG_PUBLIC_API_BASE_URL=<agenticorg-smoke-url>
```

Run the hosted smoke runner only after both temporary smoke URLs are known:

```powershell
python scripts/commerce_agent_hosted_smoke.py --run `
  --agenticorg-base <agenticorg-smoke-url> `
  --allow-agenticorg-cloud-run-url <agenticorg-smoke-url> `
  --grantex-base <grantex-smoke-url> `
  --allow-grantex-cloud-run-url <grantex-smoke-url> `
  --auth-source-env-name GRANTEX_API_KEY `
  --fixture-env .tmp/commerce-agent-real-staging.env `
  --real-staging-evidence-report docs/reports/commerce-agent-real-staging-evidence.md `
  --evidence-report docs/reports/commerce-agent-hosted-smoke-evidence.md
```

Optional Cloud Run Job mode may use the smoke-only fixture secret name instead of a local `.tmp` fixture env. The runner records the fixture secret name only and does not read or print its value:

```powershell
python scripts/commerce_agent_hosted_smoke.py --run `
  --agenticorg-base <agenticorg-smoke-url> `
  --allow-agenticorg-cloud-run-url <agenticorg-smoke-url> `
  --grantex-base <grantex-smoke-url> `
  --allow-grantex-cloud-run-url <grantex-smoke-url> `
  --auth-source-env-name GRANTEX_API_KEY `
  --fixture-secret-name agenticorg-commerce-smoke-fixture-env `
  --evidence-report docs/reports/commerce-agent-hosted-smoke-evidence.md
```

## Smoke Checks

The runner checks:

- `GET /api/v1/health/liveness`
- `GET /api/v1/health`
- `GET /api/v1/mcp/tools` includes `agenticorg_commerce_sales_agent`
- `GET /api/v1/a2a/.well-known/agent.json` uses the AgenticOrg smoke origin and Grantex smoke issuer/JWKS
- `GET /api/v1/a2a/agents` includes `commerce_sales_agent` with `grantex_commerce:*` tools

If a real-staging evidence report is supplied, `consent_exchange` is expected only when it is skipped with this exact blocker code:

```text
preexported_checkout_passport_without_granted_consent_fixture
```

If `consent_exchange` is failed, missing, or skipped with any other blocker, C3 is not passing.

## Refusal Checks

Before network use, the runner refuses:

- missing explicit `--run` for hosted HTTP checks
- non-HTTPS URLs
- AgenticOrg production origins
- Grantex production origins
- arbitrary `run.app` URLs without exact allowlists
- localhost origins
- live Commerce or live Plural flags
- unsupported Grantex auth source names
- fixture env paths outside `.tmp/`
- ambiguous local fixture and fixture secret sources
- production-looking AgenticOrg service, DB, Redis, job, or secret names
- non-smoke DB, Redis, service, job, or secret names

## Evidence Format

Evidence may include only:

- AgenticOrg and Grantex hosts
- commit SHA or image tag
- smoke resource names
- non-secret env var names
- secret names only
- fixture env var names
- synthetic fixture IDs
- redacted short hashes for sensitive fixture values
- case status, HTTP status, latency, error code, and blocker

Evidence must not include `.tmp` file contents, auth values, passports/JWT values, idempotency values, webhook material, provider material, raw payloads, DB/Redis URLs, private keys, or secret values.

## Cleanup Plan

Delete resources in this order:

```text
agenticorg-commerce-smoke-eval
agenticorg-commerce-smoke-migrate
agenticorg-api-commerce-smoke
agenticorg-commerce-smoke-redis
agenticorg-commerce-smoke-pg
agenticorg-commerce-smoke-* secrets
agenticorg-api:commerce-smoke-* image tags
```

Then verify:

- temporary AgenticOrg resources are absent
- temporary Grantex smoke resources are absent
- production AgenticOrg services still exist and were not updated
- production Grantex services still exist and were not updated
- production config, secrets, Commerce V1 flags, live payment flags, and live Plural flags are unchanged

## C3-Prep Confirmation

This C3-prep runbook and runner do not deploy, create cloud resources, change production config, enable production Commerce V1, enable live payments, enable live Plural, touch secrets, or run smoke/cloud commands.
