# OACP End-User Flow

Supersession note: the canonical AgenticOrg OACP runtime docs now live in
`docs/oacp/README.md`, with the canonical flow at
`docs/oacp/end-user-flow.md`. This root-level page is retained as
historical/contextual detail.

Status: current runtime closure guide, updated 2026-06-19.

OACP in AgenticOrg is the runtime path for seller and buyer agents. The current
shipping path is live-data capable for read-only Shopify catalog evidence,
Grantex internal artifact issuance, cached buyer answers, channel bridge
contracts, and Plural/Pine capability checks. It is not yet a final payment or
order execution path until the provider, platform, legal, and merchant gates
listed below are configured and approved.

AgenticOrg creates Seller Commerce Agent onboarding packets, starts read-only
merchant connector syncs, stores Grantex-issued artifacts in its durable cache,
answers buyer product questions, and exposes bridge adapters. Grantex remains
the trust, policy, and canonical artifact authority. Shopify and other merchant
systems remain operational systems of record. Plural/Pine and other providers
own payment or mandate execution.

## Seller Flow

1. Merchant creates or updates a Seller Commerce Agent in AgenticOrg.
2. Merchant configures connector type and credentials in AgenticOrg. Current
   runtime support is Shopify Admin GraphQL read-only credentials stored in
   tenant-aware encrypted `ConnectorConfig`.
3. AgenticOrg creates or updates an onboarding packet with status `received` or
   `sync_ready`.
4. AgenticOrg runs read-only Shopify product sync and stores normalized public
   product snapshots only. `raw_payload_stored=false`.
5. AgenticOrg sends redacted connector evidence to Grantex through the C6Z
   authority endpoint. Production should use the dedicated Grantex
   AgenticOrg-authority service token, tenant-allowlisted on the Grantex side.
6. Grantex validates the request and issues internal OACP artifacts.
7. AgenticOrg validates and caches the artifacts for non-binding use until TTL,
   revocation, freshness, scope, risk, or final-commitment rules require
   refresh.

## Buyer Flow

1. Buyer asks a product question through web, MCP, OpenAPI/function bridge, A2A,
   WhatsApp, or Telegram.
2. AgenticOrg resolves the common bridge contract and reads cached OACP
   artifacts plus normalized product snapshots.
3. The answer includes source label, freshness label, artifact refs, matched
   product facts, unsupported capabilities, and refusal reason when needed.
4. If the buyer asks to buy, pay, place an order, hold stock, create a mandate,
   refund, return, ship, or publish public discovery, AgenticOrg must either:
   produce a prepared handoff/consent path backed by fresh OACP artifacts and
   approved provider capability evidence, or refuse. It must not invent payment,
   order, inventory, delivery, refund, or return state.

## First Buyer Surfaces

AgenticOrg exposes a runtime surface matrix at:

`GET /api/v1/commerce/runtime/bridges/surfaces`

| Buyer surface | Runtime bridge | Current meaning |
| --- | --- | --- |
| Web | `/bridges/web/ask` | AgenticOrg-hosted web buyer session for grounded product Q&A. |
| ChatGPT-style | MCP seller tools, OpenAPI fallback | Uses `agenticorg-mcp-server` seller tools or OpenAPI action bridge; external ChatGPT approval is not implied. |
| Claude / Claude Code | MCP seller tools | Uses `agenticorg-mcp-server` seller tools; client configuration/approval is outside OACP artifacts. |
| Gemini-style | OpenAPI/function bridge, A2A fallback | Uses `/bridges/openapi/schema` and `/bridges/a2a/agent-card`. |
| Perplexity-style | OpenAPI hosted answer/action bridge | Uses `/bridges/openapi/schema` and `/bridges/openapi/ask`. |
| WhatsApp | `/bridges/whatsapp/webhook` | Requires WhatsApp Business Platform credentials and webhook approval. |
| Telegram | `/bridges/telegram/webhook` | Requires Telegram bot token and webhook setup. |

## Launch Boundaries

- Public discovery remains disabled.
- `allowed_to_execute=false`.
- No checkout, payment, order, mandate, refund, return, shipment, inventory
  hold, provider execution, merchant-private mutation, or live rail is enabled
  by the OACP artifact/cache path alone.
- OACP compatibility adapters are not certification, conformance,
  standardization, public publication, merchant approval, or payment approval.

## Activation Checklist For A Shopify Merchant Pilot

The runtime can move a Shopify merchant only when these are true:

- Shopify Admin API credential has read-only product, variant, media, and
  inventory access and passes validation through AgenticOrg connector setup.
- Grantex C6Z authority service token is configured on both sides and
  `COMMERCE_C6Z_AUTHORITY_SERVICE_TENANTS` allowlists the merchant tenant.
- Grantex issues all required artifact families, including catalog, price,
  inventory, public-discovery state, mandate capability, protocol adapter, and
  authority status.
- AgenticOrg caches those artifacts and answers from cache with source and
  freshness labels.
- Buyer surface credentials and approvals exist for the intended channels.
- Plural/Pine P3P capability verification succeeds and stores only
  non-sensitive evidence references.
- Final payment/order execution remains blocked until the merchant, provider,
  legal, channel, rollback, and smoke gates are explicitly approved.
