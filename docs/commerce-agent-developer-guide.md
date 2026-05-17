# Commerce Sales Agent Developer Guide

This guide explains how to run, inspect, and extend the AgenticOrg Commerce
Sales Agent without weakening the Grantex-only commerce boundary.

## Quick Rules

- Use `python demos/commerce_sales_agent_demo.py --mode=mock` for normal local
  development.
- Use `--mode=real-staging` only with explicit approval and an approved Grantex
  staging or exact temporary smoke URL.
- Use only `grantex_commerce:*` commerce tools.
- Do not call Stripe, Plural, Pine, or provider credential paths for commerce.
- Do not print fixture env values, passports/JWTs, auth material, idempotency
  values, raw payloads, DB/Redis URLs, private keys, or secrets.
- Do not present mocked output as hosted or real-staging evidence.

## Mock Demo

```powershell
python demos/commerce_sales_agent_demo.py --mode=mock
```

Mock mode is the default. It is safe for local demonstration and documentation
checks, but it is not hosted evidence and does not prove production readiness.

## Real-Staging Eval

Real-staging mode is fail-closed. It requires an approved Grantex base URL, exact
smoke allowlist for `run.app` origins, and exactly one auth source name supplied
through runtime environment outside logs.

Example shape, using placeholders only:

```powershell
python demos/commerce_sales_agent_demo.py --mode=real-staging `
  --grantex-base <approved-smoke-origin> `
  --allow-smoke-cloud-run-url <same-approved-smoke-origin> `
  --fixture-env .tmp/commerce-agent-real-staging.env `
  --evidence-report docs/reports/commerce-agent-real-staging-evidence.md
```

The fixture file path must stay under `.tmp/`. The file may contain usable
runtime material during approved runs. Never commit it, print it, or quote it in
docs, PR bodies, logs, or chat.

## Env Var Names

Evidence and docs may name variables, but must not include their values.
Common names include:

| Variable name | Purpose |
| --- | --- |
| `GRANTEX_COMMERCE_BASE_URL` | Approved Grantex commerce origin. |
| `GRANTEX_BASE_URL` | Approved Grantex base origin. |
| `AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL` | Exact allowed smoke `run.app` origin. |
| `AGENTICORG_COMMERCE_FIXTURE_ENV` | Local `.tmp` fixture file path. |
| `GRANTEX_API_KEY` | One possible Grantex auth source name. |
| `GRANTEX_COMMERCE_BEARER_TOKEN` | One possible Grantex auth source name. |
| `GRANTEX_AGENT_ASSERTION` | One possible Grantex auth source name. |

Use exactly one Grantex auth source. Ambiguous or missing auth fails before
network work.

## Refusal Behavior

| Refusal | Expected result |
| --- | --- |
| Production Grantex URL | Refused before auth/network. |
| Arbitrary `run.app` URL without exact allowlist | Refused before auth/network. |
| HTTP localhost or non-HTTPS real-staging URL | Refused before auth/network. |
| Fixture path outside `.tmp/` | Refused. |
| Fake connector/provider path | Refused by regression tests and runtime guardrails. |
| Direct provider imports/calls in commerce code | Blocked by static regression. |

## Evidence Interpretation

Evidence may record:

- host names and endpoint names;
- case status;
- HTTP status and latency;
- error or blocker codes;
- synthetic fixture IDs;
- variable names used;
- redacted short hashes.

Evidence must not record:

- bearer token values;
- Commerce Passport or JWT values;
- idempotency key values;
- webhook secrets;
- provider credentials;
- raw request or response payloads;
- DB/Redis URLs;
- private keys;
- secret values.

## Skipped And Blocked Cases

Skipped cases are acceptable only when they record a stable blocker that explains
the missing fixture or approved gate. Current fixture-backed consent exchange
behavior is expected only when the blocker is:

`preexported_checkout_passport_without_granted_consent_fixture`

Missing or inconsistent amount-cap metadata must skip the positive payment path
with an explicit blocker rather than sending an unsafe request. The separate
amount-cap breach negative case must fail locally before network or provider
work.

## Hosted Smoke

C3 hosted smoke is API-only. It verifies liveness, health, MCP tools, A2A agent
card, A2A agent listing, Grantex-only commerce tools, refusal checks, and cleanup
of temporary resources. It does not certify the full UI/worker/beat staging shape
and it does not approve production discovery or live payments.

See `docs/commerce-agent-c3-hosted-smoke-runbook.md` and
`docs/reports/commerce-agent-hosted-smoke-evidence.md`.

## Public Discovery Gate

Public MCP/A2A commerce discovery is fail-closed behind the non-secret setting
`AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED`.

| Value | Result |
| --- | --- |
| absent, empty, invalid, or any non-true value | `/api/v1/mcp/tools`, `/api/v1/a2a/.well-known/agent.json`, and `/api/v1/a2a/agents` hide `commerce_sales_agent` and `grantex_commerce:*` metadata. |
| `true`, `1`, `yes`, `on`, or `enabled` | Local/test or explicitly approved environments may expose existing Grantex-only commerce metadata. |

This gate does not remove Commerce Sales Agent code, demos, evals, or Grantex
connector behavior. It only prevents public discovery from implying production
commerce readiness before Grantex read-only production Commerce V1 discovery is
approved.

## Extending The Agent

When adding commerce behavior:

1. Keep requests built from explicit allowlists, not arbitrary fixture dicts.
2. Add tests for refusal and redaction behavior.
3. Update `tests/regression/test_commerce_sales_agent_no_provider_calls.py` if
   new commerce files are added.
4. Keep all payment-affecting work on Grantex tools.
5. Update evidence docs only with scrubbed, non-secret summaries.
