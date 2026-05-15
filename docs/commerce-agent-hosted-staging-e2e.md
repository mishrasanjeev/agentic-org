# Commerce Sales Agent Hosted Staging E2E Plan

## Purpose And Scope

This guide defines the M11 hosted staging E2E plan for the AgenticOrg Commerce Sales Agent handoff to Grantex Commerce V1. It is a documentation and command-plan artifact only. It does not deploy, merge, create cloud resources, change production config, enable live payments, enable live Plural, or write secret values.

AgenticOrg consumes Grantex staging data only. Commerce execution must stay on the Grantex Commerce API and `grantex_commerce:*` tool path.

## Required Staging Targets

- AgenticOrg base: `https://staging.agenticorg.ai`
- Grantex commerce base: `GRANTEX_COMMERCE_BASE_URL=https://api-staging.grantex.dev`
- Grantex fallback base: `GRANTEX_BASE_URL=https://api-staging.grantex.dev`

The production API origin `https://api.grantex.dev` is refused for hosted staging. It is not a staging target.

## Required Env Var Names Only

- `AGENTICORG_BASE_URL`
- `GRANTEX_COMMERCE_BASE_URL`
- `GRANTEX_BASE_URL`
- One of `GRANTEX_COMMERCE_BEARER_TOKEN`, `GRANTEX_AGENT_ASSERTION`, or `GRANTEX_API_KEY`

Record only names in docs and reports. Do not paste secret values into shell history, logs, evidence, or PR descriptions.

## Commerce Provider Boundary

No direct Stripe/Plural/Pine/provider credential commerce path is prescribed.

The Commerce Sales Agent must not call Stripe, Plural, Pine, or any payment provider directly for commerce. It must not read or handle provider credentials for commerce. It must use Grantex staging commerce endpoints and Grantex MCP tools for catalog, inventory, cart, consent, passport, payment intent, checkout, webhook evidence, and audit evidence.

Plural sandbox may remain available only for unrelated non-commerce app areas if the staging runtime already requires it. Commerce must not use Plural credentials or live provider credentials.

## Expected Staging Data

- Tenant: `cten_staging_commerce`
- Merchant: `mch_staging_electronics_pilot`
- Agent: `cag_staging_agenticorg_sales`
- Category: `electronics_appliances`
- Provider: `mock`
- Live payments: false
- Live Plural: false

## Discovery Checks

- AgenticOrg health endpoint returns healthy staging status.
- MCP discovery exposes the Commerce Sales Agent only in staging.
- MCP discovery points commerce tools to Grantex staging.
- A2A discovery exposes the staging Commerce Sales Agent card.
- A2A discovery does not expose production commerce endpoints.
- Hidden or disabled commerce discovery can be restored quickly if the staging run fails no-go criteria.

## Real Staging Demo And Eval Command Plan

The C1 real-staging mode runs local AgenticOrg demo/evals against an approved Grantex staging or smoke URL. Mock mode remains the default and must not be reported as hosted evidence.

For hosted staging with custom DNS:

```powershell
$env:AGENTICORG_BASE_URL='https://staging.agenticorg.ai'
$env:AGENTICORG_COMMERCE_REAL_STAGING='1'
$env:GRANTEX_COMMERCE_BASE_URL='https://api-staging.grantex.dev'
$env:GRANTEX_BASE_URL='https://api-staging.grantex.dev'
# Set exactly one Grantex auth env var securely outside logs.
python demos/commerce_sales_agent_demo.py --mode=real-staging --evidence-report docs/reports/commerce-agent-real-staging-evidence.md
python -m pytest tests/evals/test_commerce_sales_agent_real_staging.py -q
```

For repeatable Grantex Option A smoke, run local AgenticOrg against the approved smoke URL only after the Grantex smoke service exists and Grantex smoke checks pass. The required env names are:

- `GRANTEX_COMMERCE_BASE_URL=<approved smoke URL>`
- `GRANTEX_BASE_URL=<approved smoke URL>`
- `AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL=<approved smoke URL>`
- `AGENTICORG_COMMERCE_REAL_STAGING=1`
- exactly one of `GRANTEX_COMMERCE_BEARER_TOKEN`, `GRANTEX_AGENT_ASSERTION`, or `GRANTEX_API_KEY`

Do not place auth values in docs, logs, evidence, PRs, or shell transcripts. Arbitrary `run.app` URLs are refused unless the exact origin is allowlisted:

```powershell
$env:GRANTEX_COMMERCE_BASE_URL='<approved-smoke-run-app-origin>'
$env:GRANTEX_BASE_URL='<approved-smoke-run-app-origin>'
$env:AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL='<same-approved-smoke-run-app-origin>'
$env:AGENTICORG_COMMERCE_REAL_STAGING='1'
# Set exactly one Grantex auth env var securely outside logs.
python demos/commerce_sales_agent_demo.py --mode=real-staging --grantex-base '<approved-smoke-run-app-origin>' --allow-smoke-cloud-run-url '<same-approved-smoke-run-app-origin>' --evidence-report docs/reports/commerce-agent-real-staging-evidence.md
python -m pytest tests/evals/test_commerce_sales_agent_real_staging.py -q
```

The local mock demo remains available with `python demos/commerce_sales_agent_demo.py --mode=mock`. Do not present mocked results as hosted staging evidence.

Real-staging mode fails closed before connector creation, auth lookup, or network use when pointed at production URLs, credentialed URLs, arbitrary `run.app` URLs, or non-HTTPS URLs such as local development origins.

## Positive Hosted Checks

- MCP discovery when the approved target exposes it
- A2A discovery when the approved target exposes it
- connector health and MCP tools list
- merchant profile through Grantex staging or smoke
- catalog search and get item through Grantex staging or smoke
- inventory check through Grantex staging or smoke
- cart create through Grantex staging or smoke
- consent request through Grantex staging or smoke
- local guardrail negatives for missing consent, amount cap breach, unsupported EMI, unsupported discount, unsupported warranty, and no direct provider calls
- response summary generated by the Commerce Sales Agent without provider credentials

These cases remain skipped unless the approved synthetic Grantex consent/passport fixtures exist:

- consent exchange
- payment intent create
- checkout create
- payment status polling

## Negative Evals Against Real Staging Endpoints

- missing consent is refused
- denied consent is refused
- revoked passport is refused
- expired passport is refused
- amount cap breach is refused
- disabled merchant is refused
- untrusted agent is refused
- stale inventory is refused
- unsupported EMI claim is refused
- unsupported discount claim is refused
- unsupported warranty claim is refused
- invalid webhook signature evidence remains a Grantex-side refusal

## Redacted Evidence Format

The later hosted staging evidence should include only redacted metadata:

```json
{
  "report_type": "agenticorg-commerce-real-staging-e2e",
  "run_mode": "real-staging",
  "grantex_host": "api-staging.grantex.dev",
  "auth_source_env_name": "GRANTEX_AGENT_ASSERTION",
  "merchant_id": "mch_staging_electronics_pilot",
  "agent_id": "cag_staging_agenticorg_sales",
  "provider": "mock",
  "case_table": [
    {"case": "catalog_search", "status": "pass|fail|skipped", "http_status": 200, "latency_ms": 0, "error_code": null}
  ],
  "tool_sequence": ["grantex_commerce:catalog_search"],
  "no_provider_call_confirmation": true,
  "redaction": {
    "secret_values_recorded": false,
    "bearer_tokens_recorded": false,
    "passports_recorded": false,
    "request_correlation_values_recorded": false,
    "provider_material_recorded": false,
    "raw_payloads_recorded": false
  }
}
```

## Rollback And Gating Plan

If hosted staging commerce discovery must be hidden, remove the staging Commerce Sales Agent from MCP and A2A discovery, stop staging worker traffic, and keep the Grantex staging API online only for operator review if safe. Roll back AgenticOrg staging only. Do not change production discovery, production config, production secrets, or production traffic.

No-go conditions:

- AgenticOrg points commerce to a production Grantex URL.
- AgenticOrg calls direct Stripe, Plural, Pine, or provider credential paths for commerce.
- AgenticOrg stores or emits secret values, raw passports, or auth material in evidence.
- Negative evals succeed when they should be refused.
- Commerce discovery is visible outside staging.
