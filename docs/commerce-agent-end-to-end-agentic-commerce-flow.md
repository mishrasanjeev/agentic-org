# End-To-End Agentic Commerce Flow

This document explains the AgenticOrg side of the Grantex-powered agentic
commerce journey in plain English. It is documentation only. It does not enable
public discovery, production Commerce V1, checkout/payment creation, live
payments, live Plural, merchant approval, or any production allowlist.

## The Simple Mental Model

AgenticOrg is the buyer-agent layer. A buyer can start from a familiar chat
surface, such as ChatGPT, Claude, Gemini, WhatsApp, Telegram, a merchant website
chat widget, or an AgenticOrg-hosted session. AgenticOrg turns that buyer request
into safe Grantex-only commerce tool calls.

Grantex remains the merchant control plane. It owns merchant identity, catalog,
inventory, price, policy, consent, Commerce Passport, payment intent, order,
fulfillment, refund handoff, settlement, audit, and rollback.

```mermaid
flowchart LR
  buyer["Buyer"]
  chat["Chat surface"]
  adapter["AgenticOrg channel adapter"]
  session["AgenticOrg buyer-agent session"]
  aliases["Grantex-only tool aliases"]
  grantex["Grantex Commerce"]
  merchant["Merchant systems"]

  buyer --> chat
  chat --> adapter
  adapter --> session
  session --> aliases
  aliases --> grantex
  merchant --> grantex
```

## One-Time Buyer Setup

A buyer should not need to understand MCP, UCP, ACP, AP2, schema.org, Commerce
Passports, provider adapters, or merchant backends. The one-time buyer setup
should feel like normal account linking and permission setup.

| Step | Buyer sees | AgenticOrg does | Grantex does |
| --- | --- | --- | --- |
| 1. Pick a channel | "Use AgenticOrg in ChatGPT/Claude/Gemini/WhatsApp/Telegram/web." | Starts the correct channel adapter. | Provides approved merchant/tool capability metadata. |
| 2. Sign in or link account | "Continue with AgenticOrg" or channel-specific account linking. | Creates or resumes a buyer-agent session and binds the channel user to that session. | Does not expose private merchant or provider data. |
| 3. Set basic preferences | Preferred locale, currency, delivery region, notification path, and optional spending comfort. | Stores only safe session preferences needed for conversation and handoff. | Uses preferences only for policy checks, consent copy, and supported commerce flows. |
| 4. Understand permissions | Buyer sees that the agent can browse, draft carts, request consent, and hand off checkout only when approved. | Shows channel-specific action labels and limitations. | Provides capability state and blocks unsupported actions. |
| 5. Payment readiness | Buyer may use a provider wallet, hosted checkout, or approved payment handoff later. | Does not store raw cards, provider credentials, Commerce Passport values, JWTs, or secrets. | Owns consent, Commerce Passport issuance, payment intent, and checkout state. |
| 6. Revocation and history | Buyer can revoke permissions or ask what happened. | Shows redacted session/evidence status. | Owns revocation, audit evidence, and protected action history. |

Buyer setup is not a blanket authorization. Every payment-affecting action still
requires fresh Grantex consent and policy approval.

## One-Time Seller Setup

The seller setup happens in Grantex. AgenticOrg should explain and preview it,
but must not approve the seller or connect directly to private merchant systems.

| Step | Seller does in Grantex | AgenticOrg responsibility |
| --- | --- | --- |
| 1. Create merchant workspace | Create tenant, merchant profile, owner roles, sandbox/live split, and category preset. | Explain status labels and show that the merchant is not live yet. |
| 2. Verify business | Provide private legal/compliance artifacts outside the repo and record non-secret references. | Never display private contracts, contacts, pricing terms, or signed approvals. |
| 3. Connect existing systems | Connect storefront, catalog, ERP/PIM, inventory/WMS, OMS, logistics, payment provider, and support systems as needed. | Consume only Grantex-approved public-safe capability output. |
| 4. Prepare catalog | Normalize products, variants, images, price, tax, warranty, return summary, availability, and category data. | Show only grounded product facts returned by Grantex. |
| 5. Configure permissions | Choose whether agents may browse, draft carts, request checkout, read order status, or request support. | Render those capabilities to buyers only after Grantex approval. |
| 6. Run scans | Validate secrets, private data, stale inventory, overclaims, production-looking test IDs, and config/allowlist values. | Add refusal/eval coverage for unsafe claims and unsupported actions. |
| 7. Review gates | Legal, product, security, ops/support, rollback, smoke, and evidence owners approve. | Do not treat demo data or synthetic IDs as approval. |
| 8. Rehearse launch | Run sandbox/demo flows and confirm rollback, support, and evidence handling. | Provide demo scripts, buyer-agent walkthroughs, and blocked-path examples. |
| 9. Request rollout | Ask Grantex for the smallest approved surface, usually read-only discovery first. | Keep public discovery gated until Grantex approves the surface. |

## Regular Agentic Commerce Transaction

This is the intended transaction flow after a seller and buyer have completed
the relevant one-time setup and a specific merchant capability is approved.

```mermaid
sequenceDiagram
  participant B as Buyer
  participant C as Chat surface
  participant A as AgenticOrg
  participant G as Grantex
  participant M as Merchant systems
  participant P as Provider or checkout handoff

  B->>C: "Find a sofa under my budget"
  C->>A: Start or resume buyer-agent session
  A->>G: merchant_get_profile
  G-->>A: Approved merchant and capability state
  A->>G: catalog_search / catalog_get_item
  G->>M: Use synced catalog truth
  G-->>A: Products, prices, policies, freshness
  A->>G: inventory_check
  G-->>A: Availability and stale/unknown warnings
  A-->>B: Grounded options and caveats
  B->>A: "Prepare this one"
  A->>G: cart_create with exact variant IDs
  G-->>A: Cart draft and totals
  A-->>B: Consent explanation and checkout handoff
  B->>G: Approve or deny consent
  G-->>A: Commerce Passport only if consent and policy pass
  A->>G: payment_create_intent / checkout_create
  G->>P: Provider-neutral sandbox/live-approved path
  P-->>G: Payment status webhook or handoff status
  G-->>A: Payment/order status
  A-->>B: Status, next step, or safe refusal
```

## Normal Happy Path

1. Buyer asks for help in an existing chat interface.
2. AgenticOrg creates or resumes a buyer-agent session.
3. AgenticOrg asks Grantex which merchant capabilities are approved for that
   channel.
4. AgenticOrg searches catalog and checks inventory through Grantex only.
5. AgenticOrg explains options with grounded product IDs, prices, stock state,
   delivery caveats, return summary, and unknowns.
6. Buyer chooses an item or asks for a comparison.
7. AgenticOrg creates a cart draft in Grantex using exact variant IDs.
8. Grantex recalculates totals, policy, amount caps, eligibility, and supported
   checkout state.
9. AgenticOrg asks the buyer for consent using Grantex-provided copy.
10. Buyer approves or denies in the Grantex consent/checkout handoff.
11. Grantex issues a scoped Commerce Passport only if consent and policy pass.
12. AgenticOrg asks Grantex to create the payment intent and checkout handoff.
13. Provider interaction happens through Grantex, not AgenticOrg.
14. Grantex receives provider webhooks, reconciles status, and writes audit.
15. AgenticOrg shows buyer-safe status and next steps.
16. Post-purchase status, fulfillment, support, return, or refund answers come
    only from Grantex once those APIs exist and are approved.

## Failure And Recovery Paths

| Situation | AgenticOrg must do | Grantex must do |
| --- | --- | --- |
| Merchant not approved | Refuse public discovery or checkout and explain that the merchant is not live. | Keep capability fail-closed. |
| Channel cannot perform write actions | Offer read-only discovery and a safe handoff link. | Publish channel capability limits. |
| Product not found | Ask clarifying questions or show no-results response. | Return empty grounded search, not guessed products. |
| Price changed | Re-fetch item/cart, explain changed totals, request buyer confirmation again. | Recalculate and audit the change. |
| Inventory stale or unknown | Warn or refuse checkout promise. | Provide freshness timestamp and stale/unknown status. |
| Policy denied | Explain the safe blocker without leaking private policy. | Return blocker code and audit event. |
| Consent denied or expired | Stop checkout and offer browse/cart edit only. | Revoke or expire passport material. |
| Payment failed or pending | Show status and next supported step only. | Reconcile provider webhook and expose safe status. |
| Delivery/fulfillment unavailable | Refuse delivery promise. | Require verified logistics/OMS data before exposing status. |
| Return/refund requested | Use manual support handoff now; later call Grantex request/status APIs. | Own refund policy, provider handoff, and audit. |

## What AgenticOrg Must Never Do

- Do not call Plural, Stripe, Pine, Shopify, WooCommerce, Magento, ERP, OMS,
  WMS, logistics, support, or merchant private APIs directly for commerce
  execution.
- Do not store provider credentials, raw payment data, Commerce Passport values,
  JWTs, idempotency key values, webhook secrets, DB/Redis URLs, private keys, or
  private merchant artifacts.
- Do not invent products, sellers, prices, discounts, stock, delivery promises,
  return eligibility, order status, payment status, or refund outcomes.
- Do not claim a buyer channel is launch-ready until account linking, session
  creation, Grantex capability discovery, consent handoff, fallback behavior,
  telemetry, smoke tests, and approval state exist.
- Do not treat synthetic/demo merchants or synthetic IDs as production approval.

## Implementation Backlog

| Slice | AgenticOrg output | Dependency |
| --- | --- | --- |
| Buyer session core | Stable buyer-agent session creation/resume across channels. | AgenticOrg auth/session model. |
| Web/mobile channel | Hosted buyer-agent session and embeddable merchant link/widget. | Grantex read-only approval. |
| ChatGPT/Claude channel | Remote MCP connector/app with scopes, action labels, and smoke tests. | Platform approval plus Grantex capabilities. |
| Gemini channel | Function-calling wrapper or approved native launch path. | Gemini platform design and Grantex capabilities. |
| WhatsApp/Telegram channel | Bot/webhook adapters, identity mapping, opt-out, consent links. | Channel credentials and webhook secret handling outside Git. |
| Buyer UX | Grounded comparison, cart draft, consent copy, checkout status, refusal copy. | Grantex catalog/cart/consent/payment APIs. |
| Post-purchase UX | Order/fulfillment/support/return/refund status display. | Grantex order, fulfillment, support, and refund APIs. |
| Merchant demo UX | Demo launch rehearsal and blocked-path explanations. | Grantex merchant onboarding/readiness docs. |
| Evals | Regression tests for no invention, stale data, policy denial, direct-provider attempts, and unsafe claims. | New channel and Grantex capability slices. |
