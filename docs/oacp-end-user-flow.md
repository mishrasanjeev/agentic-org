# OACP End-User Flow

Status: current runtime closure guide, updated 2026-06-18.

OACP in AgenticOrg is a non-executing runtime path for seller and buyer agents.
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
5. AgenticOrg sends redacted connector evidence to Grantex.
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
   refund, return, ship, or publish public discovery, AgenticOrg refuses.

## Launch Boundaries

- Public discovery remains disabled.
- `allowed_to_execute=false`.
- No checkout, payment, order, mandate, refund, return, shipment, inventory
  hold, provider execution, merchant-private mutation, or live rail is enabled.
- OACP compatibility adapters are not certification, conformance,
  standardization, public publication, merchant approval, or payment approval.
