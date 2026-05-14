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

After Grantex and AgenticOrg hosted staging are both deployed, M11 should add or run a staging-safe command plan like:

```bash
python demos/commerce_sales_agent_demo.py
python -m pytest tests/evals/test_commerce_sales_agent_demo.py -q
python -m pytest tests/evals/test_commerce_sales_agent_evals.py -q
```

Expected staging run configuration:

- `AGENTICORG_BASE_URL=https://staging.agenticorg.ai`
- `GRANTEX_COMMERCE_BASE_URL=https://api-staging.grantex.dev`
- `GRANTEX_BASE_URL=https://api-staging.grantex.dev`
- Merchant ID `mch_staging_electronics_pilot`
- Agent ID `cag_staging_agenticorg_sales`
- Provider `mock`

The future hosted staging demo/eval must redact auth headers, generated Commerce Passport material, generated payment references, and any generated request correlation material from logs and reports.

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
