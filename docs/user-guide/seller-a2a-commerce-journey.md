## What this journey proves

An outside buyer agent can ask an AgenticOrg Seller Commerce Agent about a merchant's products over authenticated A2A v1 HTTP+JSON. The buyer can run in a consumer app such as Muse, Instinct or Dots, or in any other service that can make this A2A request. Those names are examples of possible clients, **not** claims of a built-in plugin, vendor partnership, verified client identity or automatic discovery.

The working seller question path is **non-binding**. It can return source-labelled product and inventory snapshots, or refuse. It cannot create an order, hold stock, collect money or create a Pine Labs Plural mandate. A complete paid purchase additionally requires merchant and provider integration, human authorization where required, provider confirmation, order creation and reconciliation. Do not tell a buyer they have purchased until those authoritative confirmations exist.

**Who can operate this journey.** Merchant staff provide product, source-system and payment-provider approvals. An AgenticOrg **tenant administrator** performs the protected Runtime Actions, connector credential/sync, artifact issuance, capability verification and outside-buyer access issuance described below. The `merchant` role can manage merchant configuration, but it cannot perform those admin-only actions. Arrange the administrator handoff before starting the checkpoints.

```flow
Merchant | Configure the seller, source system, channel approvals and provider reference in AgenticOrg.
Shopify | Sync the merchant's products, variants, prices and inventory read-only.
Grantex | Issue scoped OACP authority artifacts; AgenticOrg validates and caches them.
Buyer app | Receive merchant-approved access and connect to the A2A v1 seller endpoint.
Seller agent | Answer product questions from fresh cached evidence; refuse purchase execution.
Plural/P3P | Verify capability, then use separately approved provider and merchant paths for real authorization and settlement.
```

## Transaction map: from question to a real sale

The interactive map above lets you select an illustrative buyer app and inspect each message. Its selected app name changes the diagram, **not** the system integration. The same merchant-scoped A2A rules apply to any external buyer implementation. A consumer app operator must explicitly configure the seller endpoint and protect its merchant-issued credential.

| Hop | Who talks to whom | Buyer-visible outcome | Current state |
| --- | --- | --- | --- |
| 1 | Outside buyer to AgenticOrg seller | The buyer can discover a generic card and use approved seller access. | Available with merchant-scoped credential. |
| 2 | Seller to buyer, using cached Shopify/Grantex evidence | A product answer includes source and freshness; it is not a final offer. | Available, non-binding. |
| 3 | Buyer to seller: "buy this" | The seller refuses execution and can point to a separate prepared handoff. | Available refusal; no order or stock hold. |
| 4 | AgenticOrg verifier to Plural/P3P | Capability evidence says whether a provider-owned path may be available. | Available separately; not a charge or mandate. |
| 5 | Buyer to provider authorization | The buyer reviews terms and authorizes with an approved provider flow. | Requires merchant/provider integration; not wired through the current A2A seller route. |
| 6 | Provider outcome to merchant order system | The buyer sees confirmed only after payment and order/inventory evidence agree. | Requires verified callbacks, merchant execution and reconciliation. |

**Authority boundary.** The third-party buyer agent never needs to live on AgenticOrg. AgenticOrg hosts the seller and maintains the sourced cache; Shopify owns current commercial facts, Grantex governs the canonical artifacts, and Pine Labs Plural/P3P owns payment execution. The diagram is an explanatory journey, not a live checkout simulator.

## Checkpoint 1: create the seller

**Tenant-admin action.** Sign in as a tenant administrator and open [Commerce Runtime](https://app.agenticorg.ai/dashboard/commerce-runtime). In **Merchant Commerce Configuration**, set the merchant ID, seller agent ID, display name, source connector, allowed buyer channels, and payment provider type `plural_pine` where appropriate. Save the configuration, then use **Create** under Runtime Actions to create or update the seller onboarding packet. Merchant staff may supply or separately save configuration with their scoped role, but the tenant administrator must perform **Create**. Keep **Public catalog** off until both platform and merchant publishing approvals are in place. A public Agent Card does not publish a merchant catalog.

**Pass check.** Readiness and the onboarding packet show the intended merchant, seller, tenant and source. Keep separate IDs for separate merchants. The packet is not a transaction authority or provider approval.

## Checkpoint 2: bring source facts

**Shopify and tenant-admin action.** Have the Shopify administrator approve the read-only Admin API access and webhook setup. The AgenticOrg tenant administrator then saves the credential through AgenticOrg's protected custody path in **Shopify Connector**, inspects **Status**, and runs **Sync**. Compare a small sample of product IDs, variants, image URLs, amount/currency and inventory timestamps against Shopify. A webhook signals a possible change; it does not itself prove a fresh snapshot. Other configured ERP or commerce systems need their own validated adapter and must not be treated as Shopify-equivalent by default.

**Pass check.** The sync reports product/variant counts and a source evidence reference for the correct merchant. Prices and stock remain snapshots, not final offers or reservations. Never paste a Shopify token into an A2A message, documentation page or buyer app.

## Checkpoint 3: establish authority

**Tenant-admin action.** After a successful source sync, select **Issue** in Runtime Actions to request Grantex OACP authority for that evidence. Confirm the returned artifacts are scoped to this tenant, merchant and seller, have source references, have valid expiry/freshness, and are held in AgenticOrg's cache. Use the [OACP commerce guide](/docs/commerce) for the trust and cache model.

**Pass check.** Buyer-facing answers cite the merchant source and freshness. If Grantex is temporarily unavailable, a still-valid cached artifact can support non-binding Q&A; expired, revoked or mismatched authority must fail closed. AgenticOrg does not send every buyer question through Grantex.

## Checkpoint 4: admit an outside buyer

**Merchant action.** Choose one external buyer-agent integration and agree who operates it. The merchant tenant administrator issues a short-lived, merchant/seller/buyer-scoped credential via `POST /api/v1/a2a/commerce/buyer-access`. The token is shown only when created. Deliver it through a protected channel to the buyer application's secret store. Never put it in a prompt, source repository, URL, chat transcript or public Agent Card. Give each buyer agent a separate credential so it can be rotated or revoked independently.

**Buyer-app action.** Configure the AgenticOrg base URL and the A2A v1 HTTP+JSON endpoint. Fetch `GET /.well-known/agent-card.json` for the generic card, then `GET /api/v1/a2a/extendedAgentCard` with `A2A-Version: 1.0` and the merchant-issued bearer credential for the seller-specific card. Discovering the generic card alone grants no merchant access. The buyer app must explicitly configure the endpoint and credential; AgenticOrg cannot make every consumer app route shoppers here automatically.

```http
POST /api/v1/a2a/commerce/buyer-access
Authorization: Bearer <tenant-admin-credential>
Content-Type: application/json

{"merchant_id":"merchant-123","seller_agent_id":"seller-123","buyer_agent_id":"outside-buyer-123","expires_days":7}
```

The `buyer_agent_id` is a merchant-assigned label. Today's server authenticates possession of the issued bearer credential, not the claimed vendor brand. A client calling itself Muse, Instinct or Dots is not automatically a verified client of that company. Consult [A2A integration details](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/a2a-interoperability.md) before making external identity claims.

## Checkpoint 5: prove the conversation

**Buyer-app action.** Send a synchronous text question to `POST /api/v1/a2a/message:send`. The server derives tenant, merchant, seller and buyer scope from the credential. The client cannot override those values with message metadata. Inspect `sourceLabel`, `freshnessLabel`, `status`, `refusalReason`, `allowedToExecute` and `nonAuthoritativeForTransaction` in the response.

```http
POST /api/v1/a2a/message:send
Authorization: Bearer <merchant-issued-buyer-credential>
Content-Type: application/a2a+json
Accept: application/a2a+json
A2A-Version: 1.0

{"message":{"messageId":"question-123","role":"ROLE_USER","parts":[{"text":"Which canvas tote variants are in the latest snapshot?"}]}}
```

**Pass check.** The answer is sourced and time-qualified. A request to buy, hold stock or create a payment is refused. A wrong-merchant request is refused, an expired or revoked credential is rejected, and stale or unsupported facts are not promoted to final commitments. An external buyer credential must not grant catalog-admin, task or other tenant APIs. The current transport is synchronous text; streaming, push notifications, file parts and durable A2A conversations are not claimed.

Run the [local external-buyer demo](/docs/external-buyer-a2a-demo) to see a separate Python buyer process browse a synthetic seller and test purchase refusal and credential revocation. That demo proves the wire path, not a Shopify/Plural transaction.

## Checkpoint 6: hand off payment

**Merchant, provider and tenant-admin action.** The merchant and provider set up the Pine Labs Plural/P3P relationship and buyer authorization under the provider's terms. The AgenticOrg tenant administrator configures the provider reference and uses **Verify** to obtain non-sensitive capability evidence. When the buyer asks to proceed, that administrator can use the separate purchase-preparation path to assemble a bounded, source-aware handoff. This is not the A2A question route and it does not create a mandate or payment.

**What a real paid journey still needs.** The approved provider path must obtain any required human mandate/payment authorization, return a verified outcome, and reconcile with the merchant's order, inventory and receipt systems. AgenticOrg may show a paid or confirmed state only after the authoritative provider and merchant outcomes agree. If capability is unavailable, consent is absent, a price or stock snapshot changed, or confirmation is missing, show a blocker or pending state rather than success. [Plural/Pine capability guide](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/plural-pine-p3p-capability-verifier.md) explains the current verifier boundary.

**Current boundary.** AgenticOrg ships a provider capability verifier and non-executing preparation/reconciliation contracts. It does **not** ship a completed Plural/P3P payment or mandate through this external A2A seller route. Provider approval, merchant authorization, checkout/order execution and production rollout are separate integration work. Do not use a simulated capability response as proof of a paid transaction.

## Operational acceptance before inviting buyer traffic

| Check | Expected result |
| --- | --- |
| Merchant and buyer scope | Right seller answers; wrong tenant/merchant/seller cannot read. |
| Product freshness | Source, update time and expiry visible; stale or revoked evidence is refused. |
| Buyer credential | Short expiry, per-client scope, safe storage and immediate revocation test. |
| Purchase request | Refused on A2A Q&A; no order, hold, mandate or payment. |
| Provider handoff | Capability evidence is redacted; real provider and merchant outcomes are required for paid state. |
| Distribution | Each consumer app explicitly configures the A2A endpoint, access and UX. No automatic marketplace listing is implied. |

For an external buyer client, begin with the generic card and a protected credential, then validate the scoped card, one product question, a purchase refusal and a revocation. For an actual customer purchase, do not launch until the merchant, provider and channel have completed their own integration and production checks.
