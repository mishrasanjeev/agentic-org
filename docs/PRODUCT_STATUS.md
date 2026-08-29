# Current Product Status

Last verified: 2026-08-29

This page is the concise capability truth for AgenticOrg. It distinguishes
shipped runtime behavior from tenant configuration and external-provider
availability. Live version and registry totals remain available from
[`GET /api/v1/product-facts`](https://app.agenticorg.ai/api/v1/product-facts).

## Production Baseline

- Managed production runs separate API and UI services on Google Cloud Run.
- The current rollout path is migration-first and commit-pinned.
- Health reports the deployed commit and checks PostgreSQL and Redis.
- Required main-branch CI/CD, CodeQL, and RAG quality gates are release gates.
- Public commerce discovery remains off unless explicitly enabled by platform
  and merchant configuration.

The last verified release used commit
`2348d6ba839a22005912379f7c84f2d9fdba0c27`. This commit is retained as release
evidence, not as a permanent version string. Check the live endpoints for the
current deployment.

## Capability Matrix

| Area | Shipped runtime | Configuration or external dependency | Explicit boundary |
| --- | --- | --- | --- |
| Agents | Tenant-created agents, built-in definitions, model routing, tools, persisted runs, approvals, evaluation, audit, and kill-switch behavior | Model credentials or local model runtime; tenant policy and tool grants | An agent definition is not authorization for an external action |
| Workflows | Definitions, durable runs, schedules, conditions, approval steps, retry/error states, and reviewable history | Worker/scheduler deployment for background execution; connector credentials | A workflow cannot bypass tenant, scope, approval, or provider checks |
| Knowledge and OCR | Native extraction for text, data, email, PDF, Office, OpenDocument, RTF, and image uploads; OCR for scanned PDFs and images; provenance-aware chunks and tenant search | Production image tools and installed language packs; model/embedding service for semantic retrieval | Corrupt, unsupported, oversized, or zero-text files fail explicitly; audio/video are not document uploads |
| Voice | Signed Twilio webhook runtime, provider-managed STT/TTS, encrypted bounded transcripts, masked call history, runtime health, and explicit outbound-call API | Twilio account, number, credentials, mapped active agent, callback configuration, and call charges | No unsigned webhook; no paid call without an approved destination; other voice providers remain configuration-only until their workers ship |
| RPA | Built-in Playwright script discovery, tenant-scoped execution, schedules, durable history, screenshots/results, timeout handling, and approved-domain egress controls | Browser runtime, tenant-approved domains, credentials, and target-site stability | RPA runs are explicit external actions; catalog presence does not authorize a run or bypass anti-bot/provider policy |
| Connectors | Native connector registry, health/configuration surfaces, scoped tool gateway, optional integration gateways, and merchant-scoped Shopify credential custody | Provider accounts, scopes, secrets, rate limits, and tenant approval | A listed connector is not proof of a live tenant connection |
| Developer surfaces | REST, OpenAPI, Python SDK and CLI, TypeScript SDK, MCP server, and A2A discovery/task surfaces | Compatible client, authentication, tenant/company context, and server version | Discovery metadata does not create tool or transaction authority |
| OACP commerce | Merchant config, Seller Commerce Agent onboarding, real Shopify read-only Admin GraphQL sync, signed Shopify webhook rejection, Grantex authority request, durable OACP cache, buyer-safe Q&A, protocol payloads, web/MCP/OpenAPI/A2A/WhatsApp/Telegram bridge routes, Plural/Pine capability verification, purchase preparation, and Offline POS handoff/reconciliation | Shopify, Grantex, channel, provider, and POS credentials or approvals; merchant publishing setting | AgenticOrg does not invent paid/order state or own provider/POS execution |
| Billing and operations | Hosted plan catalog, billing routes, health, migrations, observability hooks, security checks, and reviewed Cloud Run rollout helper | Payment-provider configuration and operational ownership | Billing integration is separate from OACP buyer-payment execution |

## OACP Runtime Truth

```mermaid
flowchart LR
  merchant[Merchant and Shopify] --> seller[AgenticOrg Seller Commerce Agent]
  seller --> authority[Grantex trust and artifact authority]
  authority --> cache[AgenticOrg OACP cache]
  cache --> buyer[Buyer agent and approved channels]
  buyer --> handoff[Prepared provider or POS handoff]
  handoff --> source[Provider, POS, or merchant confirmation]
```

The deployed AgenticOrg code can perform the real read-only Shopify connector
path when merchant credentials are configured. It can validate and cache OACP
artifacts, answer from fresh evidence, expose bounded protocol-adapter payloads,
and prepare provider or POS handoffs. Grantex is not required for every
non-binding buyer message when a valid cached artifact exists.

OACP does not make AgenticOrg the payment processor, merchant system, or order
system. Provider, POS, bank, and merchant callbacks remain authoritative for
their outcomes.

## Not Universal Or Not Shipped

- WooCommerce, ERP, PIM, OMS, WMS, and custom commerce sources do not yet have
  the same shipped runtime adapter as Shopify.
- ChatGPT, Claude, Gemini, Perplexity, WhatsApp, and Telegram require their own
  client configuration, marketplace/channel approval, credentials, and rollout.
  Protocol payloads and bridge routes do not create those approvals.
- AgenticOrg does not execute OACP payment capture, mandates, refunds, shipping,
  inventory holds, or order creation from cached artifacts.
- Plural/Pine verification proves configured capability evidence, not a
  completed buyer payment.
- Voice providers other than the signed Twilio path are not production runtime
  claims.
- Public OACP standardization, certification, or third-party conformance is not
  claimed.

## Verification Pointers

- [Production smoke runbook](runbooks/production_smoke.md)
- [Knowledge ingestion and OCR](knowledge-ingestion.md)
- [Voice runtime](voice-runtime.md)
- [RPA runtime](rpa-runtime.md)
- [SDK release process](release-sdks.md)
- [OACP truth inventory](oacp/truth-inventory.md)
- [OACP operations](oacp/runtime-operations-runbook.md)
- [Deployment guide](deployment.md)
