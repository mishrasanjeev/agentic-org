# Commerce Sales Agent Developer Guide

This guide explains how to run, inspect, and extend the AgenticOrg Commerce
Sales Agent without weakening the OACP runtime boundary.

## Quick Rules

- Use `python demos/commerce_sales_agent_demo.py --mode=mock` for normal local
  development.
- Use `--mode=real-staging` only with explicit approval and an approved Grantex
  staging or exact temporary smoke URL.
- AgenticOrg may own Seller Commerce Agent onboarding, Shopify Admin GraphQL
  read-only sync, OACP artifact cache consumption, buyer sessions, bridge
  adapters, and provider-owned mandate capability verification.
- Use Grantex for trust, policy, and canonical OACP artifact authority. Do not
  make Grantex a toll booth for valid cached non-binding buyer/seller answers.
- Do not call Stripe, Plural, Pine, POS, or provider payment execution paths for
  commerce. Provider-owned capability verification is allowed only through the
  non-executing verifier and must store redacted evidence refs only.
- Treat `grantex_commerce:buyer_discovery_preview` as read-only sandbox preview
  data. It is not public discovery, production approval, checkout/payment,
  fulfillment, returns, refunds, or live provider access.
- Use `api/v1/commerce_runtime.py` bridge endpoints for buyer-facing OACP
  runtime questions. Web, MCP, OpenAPI/function, A2A, WhatsApp, and Telegram
  adapters must call the same cache-backed answer path.
- Use the OACP cache helpers only for public-safe, non-binding preview,
  prepared-only handoff, or maintenance planning. Cache records and maintenance
  plans must keep `allowed_to_execute=false`.
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
| Provider payment/order/mandate execution imports/calls in commerce code | Blocked by static regression. |
| OACP cache record with missing scope, stale freshness, revoked or ambiguous revocation, private/raw refs, executable flags, or false non-enablement flags | Refused or quarantined by cache evaluation/maintenance planning. |
| Buyer discovery asks for checkout/payment/live provider/fulfillment/refund work | Refused by the C6I buyer discovery session wrapper before any non-read-only path. |
| Buyer discovery asks for unrelated unsupported work | Refused by the C6I buyer discovery session wrapper. |

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
card, A2A agent listing, commerce runtime tools, refusal checks, and cleanup
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
| `true`, `1`, `yes`, `on`, or `enabled` | Local/test or explicitly approved environments may expose existing gated commerce metadata. |

This gate does not remove Commerce Sales Agent code, demos, evals, or Grantex
connector behavior. It only prevents public discovery from implying production
commerce readiness before the runtime and authority evidence is approved.

## OACP Artifact Cache Development

C6X4 adds durable local OACP cache records. C6X5 adds maintenance planning over
those records. Treat both as internal support for non-binding preview, answer,
and prepared-only handoff behavior.

When working on cache code:

1. Store only public-safe source refs and redacted evidence refs.
2. Do not store raw provider payloads, raw connector payloads, raw JWTs,
   credentials, tokens, private keys, bank/card data, private customer data,
   private merchant API values, production allowlists, or executable targets.
3. Preserve buyer-agent, seller-agent, tenant, and merchant scope checks.
4. Preserve TTL, freshness, revocation snapshot, risk tier, and source posture
   checks.
5. Keep `allowed_to_execute=false`, `non_authoritative_for_transaction=true`,
   `no_checkout_payment_enablement=true`, `no_live_provider_enablement=true`,
   and `no_public_discovery_enablement=true`.
6. Maintenance planning may recommend refresh, eviction, purge, quarantine,
   source refresh, or human review, but it must not perform those side effects.
7. Do not add a scheduler, queue, cron, durable maintenance log, Grantex live
   call, provider call, or merchant private API call without a separate approved
   slice.

Focused tests:

```powershell
python -m pytest tests/unit/test_oacp_c6x4_durable_cache_repository.py tests/unit/test_oacp_c6x5_cache_maintenance.py -q
```

## Extending The Agent

When adding commerce behavior:

1. Keep requests built from explicit allowlists, not arbitrary fixture dicts.
2. Add tests for refusal and redaction behavior.
3. Update `tests/regression/test_commerce_sales_agent_no_provider_calls.py` if
   new commerce files are added.
4. Keep all payment-affecting work out of OACP runtime artifacts unless a
   separate execution controller is approved.
5. Update evidence docs only with scrubbed, non-secret summaries.
6. For buyer discovery, consume only cached OACP artifacts or read-only preview
   payloads and do not expose operator handoff or withdrawal endpoints.
7. For OACP cache behavior, update the C6X report docs and preserve the
   no-publication, no-certification, no-production-readiness, and no-execution
   boundaries.

## C6I Rollback

C6I adds only local AgenticOrg orchestration, prompt, docs, and tests. Roll back
by reverting the buyer session wrapper usage. No production config, public
discovery setting, allowlist, provider credential, checkout/payment state,
fulfillment state, refund state, or merchant private API integration is changed.

## Commerce Runtime Requirements

The C6Z runtime closure adds these AgenticOrg-owned paths:

| Area | Runtime surface | Boundary |
| --- | --- | --- |
| Seller onboarding | `POST /api/v1/commerce/runtime/seller-agents/onboarding-packets` | Tenant/merchant/seller-agent scoped, non-executing. |
| Shopify connector credentials | `POST /api/v1/commerce/runtime/seller-agents/connectors/shopify/credentials` | Tenant-aware encrypted `ConnectorConfig`; response redacts values. |
| Shopify sync | `POST /api/v1/commerce/runtime/seller-agents/shopify/sync` | Admin GraphQL reads only; stores normalized public-safe snapshots. |
| Grantex authority | `POST /api/v1/commerce/runtime/authority/grantex/request` | Sends redacted evidence for artifact issuance. |
| Artifact cache | `POST /api/v1/commerce/runtime/artifacts/cache` | Validates artifacts before storing cache records. |
| Buyer answer | `POST /api/v1/commerce/runtime/buyer-sessions/ask` | Answers from cache with source/freshness labels. |
| Bridge adapters | `/bridges/web`, `/bridges/openapi`, `/bridges/a2a`, `/bridges/whatsapp`, `/bridges/telegram` | One common non-executing bridge contract. |
| Bridge surface matrix | `GET /api/v1/commerce/runtime/bridges/surfaces` | Lists web, ChatGPT-style, Claude, Gemini-style, Perplexity-style, WhatsApp, and Telegram bridge readiness plus required channel config. |
| Plural/Pine capability | `POST /api/v1/commerce/runtime/providers/plural-pine/mandate-capability/verify` | Capability metadata only; no mandate or payment execution. |
| Offline POS readiness | `GET /api/v1/commerce/runtime/pos/offline/readiness` | Shows simulator and real POS-provider configuration posture. |
| Offline POS handoff | `POST /api/v1/commerce/runtime/pos/offline/handoffs` | Builds a non-executing POS handoff packet from prepared purchase output. |
| Offline POS confirmation | `POST /api/v1/commerce/runtime/pos/offline/confirmations` | Accepts verified POS/provider callback evidence refs only; no raw payload storage. |
| Offline POS simulator | `POST /api/v1/commerce/runtime/pos/offline/simulator/confirm` | Deterministic local confirmation for tests; cannot create live paid states. |

## Historical Commerce Alias Requirements

The aliases below describe older Grantex Commerce payment-control pilot
surfaces. They are not the OACP runtime artifact path. Do not use them to claim
checkout, payment, order, mandate, refund, return, shipment, live-provider, or
public discovery readiness.

| Future area | AgenticOrg behavior before approved execution support exists | Required behavior after approved support exists |
| --- | --- | --- |
| Order status | Refuse or explain that order status is unavailable. | Future merchant/provider confirmed status; not enabled by OACP runtime artifacts. |
| Fulfillment and shipment | Refuse delivery promises. | Future merchant/carrier confirmed status; not enabled by OACP runtime artifacts. |
| Returns and refunds | Refuse refund execution and route to merchant support/manual handoff. | Future merchant/provider workflow; not enabled by OACP runtime artifacts. |
| Offers, discounts, EMI, rewards | Do not invent promotions or affordability. | Show only sourced metadata when a future approved artifact or connector supports it. |
| UCP/ACP/schema.org/AP2 readiness | Do not claim compliance or certification. | Display compatibility-adapter metadata only with no certification or standardization claim. |

See `docs/commerce-agent-agentic-commerce-implementation-prd.md` for the full
gap register and fast-track plan.

## Future Channel Adapter Requirements

Buyer channels such as ChatGPT, Claude, Gemini, WhatsApp, Telegram, web/mobile,
and future agent marketplaces must be implemented as thin AgenticOrg channel
adapters. They may translate message formats and session identity, but they must
not implement commerce execution inside the bridge adapter.

Each channel adapter must define:

- channel type and platform capability limits;
- auth/account-linking model;
- buyer session creation and resume behavior;
- message, attachment, locale, currency, and identity normalization;
- which actions are read-only, consent-required, checkout-capable, or blocked;
- OACP artifact and bridge mapping;
- consent and checkout handoff copy;
- redacted evidence fields;
- rate-limit, retry, and human escalation behavior;
- smoke tests and regression tests.

Do not mark a channel launch-ready until a real user can start from that channel
without developer setup and the channel has documented fallback behavior when it
cannot perform write actions.
