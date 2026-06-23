# OACP End-User Flow

This is the canonical AgenticOrg OACP end-to-end flow.

```mermaid
flowchart LR
  merchant[Shopify merchant] --> seller[Seller Commerce Agent]
  seller --> sync[Read-only Shopify sync]
  sync --> grantex[Grantex authority request]
  grantex --> cache[AgenticOrg OACP cache]
  cache --> buyer[Buyer agent]
  buyer --> surfaces[Web, MCP, OpenAPI, A2A, WhatsApp, Telegram]
  buyer --> handoff[Prepared purchase or mandate handoff]
  handoff --> pos[Offline POS handoff when selected]
  pos --> reconcile[POS confirmation and reconciliation]
```

## Flow

1. Merchant creates a Seller Commerce Agent.
2. Merchant connects Shopify through AgenticOrg credential custody.
3. AgenticOrg runs read-only sync for products, variants, price, images, status, and inventory.
4. AgenticOrg requests Grantex OACP authority artifacts.
5. AgenticOrg caches signed/internal OACP artifacts with source and freshness labels.
6. Buyer asks through a supported surface.
7. Buyer agent answers from valid cache, refreshes, prepares a non-executing handoff, or refuses.
8. If the buyer wants in-store pickup or payment, AgenticOrg can create a non-executing Offline POS handoff packet from the prepared purchase.
9. POS/provider confirmation intake records `accepted`, `price_changed`, `out_of_stock`, `expired`, `needs_staff_review`, `unsupported`, `payment_pending`, `payment_confirmed`, `payment_failed`, or `receipt_available`.
10. Provider/merchant/POS systems own final payment and order execution. AgenticOrg stores only non-sensitive evidence refs and buyer-safe reconciliation status.

## User Labels

Buyer-facing answers must show source and freshness. Example: `Source: Shopify via Grantex artifact`. If a request asks to purchase, the response must say whether it is a prepared handoff, POS accepted pending staff/payment confirmation, or an exact blocker.

## When Grantex Is Unavailable

If cached artifacts remain valid for a non-binding question, AgenticOrg can continue answering with source/freshness labels. Commitment-bound requests must refresh, prepare no execution, or refuse.

## When Artifacts Are Stale

The runtime must stop commitment-bound behavior and ask for Shopify sync plus Grantex refresh. It must not fall back to raw Shopify payloads, guessed prices, or simulated POS success.
