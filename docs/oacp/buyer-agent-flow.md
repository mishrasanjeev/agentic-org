# Buyer Agent Flow Guide

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md).

Buyer agents answer from cached OACP artifacts. They do not query raw Shopify or provider secrets during buyer Q&A.

```mermaid
flowchart TD
  ask[Buyer asks] --> cache{Valid cache?}
  cache -->|Yes| risk{Low-risk question?}
  risk -->|Yes| answer[Answer with source/freshness]
  risk -->|No| prepare[Purchase/mandate preparation]
  cache -->|No| refresh[Refresh or refuse]
  prepare --> result{Checks pass?}
  result -->|Yes| handoff[Prepared handoff]
  result -->|No| blocker[Exact blocker]
```

## What Buyers Can Ask

- Product discovery and comparison.
- Availability and source/freshness.
- Merchant policy questions from valid artifacts.
- Prepared handoff requests.

## What Must Be Blocked

Paid-state claims, order creation, checkout creation, stock holds, mandate setup, refunds, returns, shipment, and private merchant-system mutation must be blocked unless an approved execution path confirms the result.
