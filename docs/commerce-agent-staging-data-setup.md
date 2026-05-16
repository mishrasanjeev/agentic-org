# Commerce Sales Agent Staging Data Setup

Status: M10 planning and doc validation only. This pass does not deploy, create cloud resources, change production config, enable live payments, enable live Plural, or write secret values.

## Purpose

AgenticOrg staging should consume the synthetic Grantex Commerce V1 staging data prepared for M10. The Commerce Sales Agent must use Grantex staging APIs and MCP tools only. AgenticOrg does not own merchant catalog seed data, payment state transitions, Commerce Passport issuance, provider webhooks, or audit timeline records.

## Expected Grantex Staging IDs

- Tenant: `cten_staging_commerce`
- Merchant: `mch_staging_electronics_pilot`
- Agent: `cag_staging_agenticorg_sales`
- Category: `electronics_appliances`
- Provider: `mock`
- Grantex API base: `https://api-staging.grantex.dev`
- Option A smoke data source: Grantex manifest `docs/examples/commerce-staging-seed.manifest.json`

AgenticOrg should treat these as synthetic staging identifiers only. They are not production customer identifiers.

## Required Staging Connector Env Names

Set these non-secret values on AgenticOrg staging services:

```bash
AGENTICORG_BASE_URL=https://staging.agenticorg.ai
GRANTEX_COMMERCE_BASE_URL=https://api-staging.grantex.dev
GRANTEX_BASE_URL=https://api-staging.grantex.dev
PLURAL_ENV=sandbox
```

Provide exactly one staging-only Grantex auth material source by secret name:

- `GRANTEX_COMMERCE_BEARER_TOKEN`
- `GRANTEX_AGENT_ASSERTION`
- `GRANTEX_API_KEY`

Do not place values for those names in docs, logs, fixtures, local env files, or PR bodies.

## Commerce Routing Rules

- Commerce actions must use `grantex_commerce:*` tools.
- AgenticOrg consumes Grantex staging data only.
- No direct Stripe/Plural/Pine/provider credential commerce path is prescribed.
- No provider credential handling is required for the Commerce Sales Agent.
- Non-commerce app areas may keep their existing provider integrations, but commerce must not use them.
- Payment intent creation, checkout creation, payment status polling, mock webhook outcomes, reconciliation, and audit evidence are Grantex responsibilities.

## Staging Demo And Eval Command Plan For Later

For C1, AgenticOrg can run locally against an approved Grantex staging or smoke URL. This proves the connector/demo/eval path against real Grantex endpoints without deploying AgenticOrg hosting.

```bash
AGENTICORG_COMMERCE_REAL_STAGING=1
GRANTEX_COMMERCE_BASE_URL=https://api-staging.grantex.dev
GRANTEX_BASE_URL=https://api-staging.grantex.dev
AGENTICORG_COMMERCE_EVIDENCE_REPORT=docs/reports/commerce-agent-real-staging-evidence.md
python demos/commerce_sales_agent_demo.py --mode=real-staging --evidence-report docs/reports/commerce-agent-real-staging-evidence.md
python -m pytest tests/evals/test_commerce_sales_agent_real_staging.py -q
```

If the approved target is a temporary Cloud Run smoke URL, set `AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL` to that exact origin. Arbitrary `run.app` origins are refused.

Expected real-staging run configuration:

- `AGENTICORG_BASE_URL=https://staging.agenticorg.ai`
- `GRANTEX_COMMERCE_BASE_URL=https://api-staging.grantex.dev`
- `GRANTEX_BASE_URL=https://api-staging.grantex.dev`
- `GRANTEX_COMMERCE_BASE_URL=<approved smoke URL>` for temporary Option A smoke
- `GRANTEX_BASE_URL=<approved smoke URL>` for temporary Option A smoke
- `AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL=<approved smoke URL>` only for temporary smoke runs
- `AGENTICORG_COMMERCE_REAL_STAGING=1`
- exactly one of `GRANTEX_COMMERCE_BEARER_TOKEN`, `GRANTEX_AGENT_ASSERTION`, or `GRANTEX_API_KEY`
- Merchant ID `mch_staging_electronics_pilot`
- Agent ID `cag_staging_agenticorg_sales`
- Provider `mock`

The real-staging demo/eval must redact auth headers, generated Commerce Passport material, generated payment references, and any generated request correlation material from logs and reports.

## C2C Local Fixture Bridge

For approved Option A smoke runs, Grantex may export a local fixture env file to `.tmp/commerce-agent-real-staging.env`. AgenticOrg can load it with `--fixture-env .tmp/commerce-agent-real-staging.env` or `AGENTICORG_COMMERCE_FIXTURE_ENV=.tmp/commerce-agent-real-staging.env`.

The fixture env file is disabled by default and must stay under `.tmp/`. It may contain the approved smoke URL, synthetic merchant/agent/product/variant IDs, exactly one Grantex auth source value, and optional synthetic passport fixture values. Usable passports, bearer tokens, agent assertions, API keys, idempotency keys, webhook secrets, and consent exchange material are sensitive runtime material even when synthetic.

AgenticOrg evidence may record variable names, synthetic IDs, redacted hashes, case status, HTTP status, latency, and error code only. It must not print or persist fixture values in docs, tests, git diffs, evidence reports, logs, PR bodies, or chat.

## Option A Smoke Coverage

The Grantex Option A smoke manifest provides enough synthetic data for these C1 local-to-smoke cases:

- connector health and tool discovery
- merchant profile
- catalog search
- catalog get item
- inventory check
- cart create
- consent request
- local guardrail refusal for missing consent
- local guardrail refusal for amount cap breach
- local guardrail refusal for unsupported EMI, discount, and warranty claims
- no direct provider call regression

These remain skipped unless the approved smoke run also provisions synthetic consent/passport fixtures and safe payment references for the AgenticOrg eval:

- consent exchange
- payment intent create
- checkout create
- payment status polling
- denied, revoked, or expired passport cases
- disabled merchant and untrusted agent cases
- invalid webhook signature and replay checks, which stay Grantex-side evidence

With `.tmp` fixture bridge data present, AgenticOrg can attempt consent exchange, payment intent creation, checkout creation, payment status polling, amount-cap refusal, and passport negative guardrails against the approved Grantex target. Missing fixture fields keep the corresponding cases explicitly skipped.

## Expected Positive Cases

- Discover the Grantex staging commerce surface.
- Search the electronics/appliances catalog.
- Get an item and at least one variant.
- Check inventory.
- Create a cart.
- Request consent.
- Exchange approved consent through Grantex.
- Create a mock-provider payment intent through Grantex.
- Create checkout through Grantex.
- Poll payment status through Grantex.
- Read audit-safe outcomes.

## Expected Negative Cases

- Denied consent.
- Missing consent.
- Revoked Commerce Passport.
- Expired Commerce Passport.
- Amount cap exceeded.
- Disabled merchant.
- Untrusted agent.
- Stale inventory.
- Unsupported EMI claim.
- Unsupported discount claim.
- Unsupported warranty claim.
- Invalid webhook signature.
- Attempted direct provider call.
- Attempted provider credential handling.

## Safety Guardrails

- Do not deploy during M10.
- Do not create cloud resources during M10.
- Do not change production config.
- Do not enable production Commerce V1.
- Do not enable live payments.
- Do not enable live Plural.
- Do not write real secret values.
- Do not commit `.tmp`, synthetic env files containing generated auth material, generated Commerce Passport material, generated request correlation material, provider credentials, or secrets.
- Do not point staging demos at `https://api.grantex.dev`.
- Do not point staging demos at `https://app.agenticorg.ai`.
- Do not run commerce through direct Stripe, Plural, Pine, or provider credential paths.

## M10 Confirmation

This document defines staging data consumption only. It does not create resources, deploy, merge, change production config, enable production Commerce V1, enable live payments, enable live Plural, or write secret values.
