# UCP Checkout, Cart And Order

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md). Arbiter and ledger:
[arbiter-ledger.md](arbiter-ledger.md).

AgenticOrg serves the Universal Commerce Protocol (UCP) transaction capabilities for a merchant
at `https://<merchant serving host>/ucp`: checkout sessions (`dev.ucp.shopping.checkout`), carts
(`dev.ucp.shopping.cart`) and orders (`dev.ucp.shopping.order`), with the fulfillment, buyer
consent, loyalty, AP2 mandate and (2026-08-25) payment authentication, split payment and payment
terms extensions. UCP `2026-04-08` is the default release; `2026-08-25` is served when
`AGENTICORG_UCP_VERSIONS` lists it (then 2026-08-25 is the profile at `/.well-known/ucp` and
2026-04-08 its `supported_versions` leaf). The profile, catalog and location capabilities are in
[ucp-profile.md](ucp-profile.md); the transaction capabilities and payment handlers below are
registered with the same profile registry and advertised only while the checkout flag is on, so
a platform negotiates exactly what the profile lists.

## Flag

`AGENTICORG_UCP_CHECKOUT_ENABLED` (default `false`).

- **Off:** every `/ucp` route answers 404. Nothing is created, decided or executed.
- **On:** checkout sessions, carts and orders are served. A checkout completes only when the
  merchant's UCP settings name a registered executor **and** the commerce arbiter seals an
  `allow` decision for the exact session payload, which the executor consumes once. The Plural
  executor (`core/commerce/executors/plural.py`, name `plural_pine`) is registered only while
  `PLURAL_PINE_LIVE_EXECUTION_ENABLED=true`; without it a valid Complete answers the business
  message `executor_unavailable` and the session stays `ready_for_complete`
  ([Plural executor](plural-executor.md)).

The flag also gates the order-webhook sweep. The merchant runtime endpoints under
`/api/v1/commerce/runtime/ucp` are tenant-administrator routes and are not gated.

## Hosting And Authentication

- The platform authenticates with the API key the merchant issued to it (UCP's pre-shared
  platform key), sent as `X-API-Key: <key>` or `Authorization: Bearer <key>`; the key is a tenant
  API key with the scopes `ucp:read` (GET) and `ucp:write` (writes). No role holds these scopes;
  tenant administrators pass.
- The merchant is resolved from the `Host` header through the profile publisher's host index:
  the host must be the merchant's attested serving host and the merchant must be published (an
  active signing key and a live `public_discovery` artifact), as for `/.well-known/ucp`, and it
  must belong to the key's tenant. Anything else is 404.
- Every write records its declared ledger event (`commerce.ucp.checkout.create`, `.update`,
  `.complete`, `.cancel`, `commerce.ucp.cart.create`, `.update`, `.cancel`); refusals and
  idempotent replays are recorded in their own transaction.

## Operations (REST binding)

| Operation | Route | Success |
|---|---|---|
| Create checkout | `POST /ucp/checkout-sessions` | 201 |
| Get checkout | `GET /ucp/checkout-sessions/{id}` | 200 |
| Update checkout (full replacement) | `PUT /ucp/checkout-sessions/{id}` | 200 |
| Complete checkout | `POST /ucp/checkout-sessions/{id}/complete` | 200 |
| Cancel checkout | `POST /ucp/checkout-sessions/{id}/cancel` | 200 |
| Create cart | `POST /ucp/carts` | 201 |
| Get / update cart | `GET` / `PUT /ucp/carts/{id}` | 200 |
| Cancel cart | `POST /ucp/carts/{id}/cancel` | 200 |
| Get order | `GET /ucp/orders/{id}` | 200 |

Headers: `UCP-Agent: profile="<platform profile URL>"` is required; `Idempotency-Key` is honoured
on every write; `Request-Id` is echoed. Business outcomes (out of stock, an unknown item, an
undeliverable address, a refused payment) are HTTP 200 with `messages[]`. Protocol errors use
4xx/5xx: 400 malformed request, 404 unknown host or resource, 409 a terminal or frozen session or
an idempotency conflict, 503 when idempotency records cannot be stored.

The same operations are callable without HTTP through `core.commerce.ucp.handlers.OPERATIONS`
(keys are the UCP MCP tool names: `create_checkout`, `get_checkout`, `update_checkout`,
`complete_checkout`, `cancel_checkout`, `create_cart`, `get_cart`, `update_cart`, `cancel_cart`,
`get_order`), which an MCP binding calls with the same request object.

## MCP Tools

`agenticorg-mcp-server` (5.1.0 and later) exposes the same operations under the UCP 2026-04-08
MCP binding names (`services/shopping/mcp.openrpc.json`): `create_checkout`, `get_checkout`,
`update_checkout`, `complete_checkout`, `cancel_checkout`, `create_cart`, `get_cart`,
`update_cart`, `cancel_cart` and `get_order`. The program's `checkout.*` naming is replaced by
the binding's names.

- **Gating.** The server registers a tool only when `AGENTICORG_UCP_PROFILE_URL` names the
  merchant's `/.well-known/ucp` and that profile lists the tool's capability at 2026-04-08
  (checkout, cart or order). The profile lists them only while `AGENTICORG_UCP_CHECKOUT_ENABLED`
  is on, so with the flag off no tool is registered, and a call made after the flag is turned
  off gets the routes' 404 as a tool error. With both releases enabled the 2026-08-25 root's
  `supported_versions` leaf for 2026-04-08 is followed. The REST endpoint must be on the
  profile's origin (https; loopback http for local development), so the credential is never sent
  elsewhere. No API change was needed.
- **Arguments.** `meta` (`ucp-agent.profile`, `idempotency-key`), `id`, and the `checkout` or
  `cart` object, mapped to the `UCP-Agent`, `Idempotency-Key` and `Request-Id` headers and the
  REST body. `complete_checkout`, `cancel_checkout` and `cancel_cart` require
  `meta.idempotency-key`, as the binding does. `meta.ucp-agent.profile` defaults to
  `AGENTICORG_UCP_AGENT_PROFILE`.
- **Decision.** `complete_checkout` sends a Complete only with exactly one of
  `decide_only: true` (the arbiter seals a `session_complete` decision; nothing executes) or a
  `decision_id` (the API consumes that allow decision once through `require_allow_decision`). A
  call with neither, with both, or with values that conflict with the checkout's
  `ai.agenticorg.commerce` member is refused locally with `decision_required` or
  `invalid_request`. Only Complete has a decision: create, update and cancel move no money and the
  arbiter has no action for them, so they are protected by the `ucp:write` scope and idempotency
  instead.
- **Annotations.** Reads: `readOnlyHint`, `idempotentHint`. Creates: neither destructive nor
  idempotent. Updates (full replacement) and cancels: `destructiveHint`, `idempotentHint`.
  Complete: `destructiveHint`, not idempotent. All: `openWorldHint`.
- **Results and errors.** HTTP 200/201 bodies, including business outcomes and
  `capabilities_incompatible`, are tool results in `structuredContent` and as text, as the binding
  requires. The vendor member keeps only the decision's `decision_id`, `action`, `outcome`,
  `failing_rule`, `payload_hash` and `expires_at`. Any other status is an MCP tool error
  (`isError`) with `{status, code, content}` taken only from the UCP error body (`{code, content}`
  or the first error message); framework details and internal refusal details are dropped.
- **Profile.** No `mcp` transport is registered in `core.commerce.ucp.registry`: the server is a
  local stdio adapter run by the platform, not a streamable HTTP endpoint on the merchant's host,
  so the profile keeps advertising REST only. Catalog tools are not exposed by it.

## Negotiation

1. `UCP-Agent` is parsed as an RFC 8941 dictionary; a missing or non-absolute profile URL is 400
   `invalid_profile_url`. A `version` parameter on the member is the platform's declared release:
   an unsupported one is 422 `version_unsupported` before any fetch.
2. The platform profile is fetched over HTTPS only, from a public address, with no redirects, a
   256 KiB cap and a cache of 60 s to 1 h (`Cache-Control: max-age`). Unreachable is 424
   `profile_unreachable`; not a profile is 422 `profile_malformed`; an unsupported
   `ucp.version` is 422.
3. Capabilities are intersected: names both sides declare at the release, then extensions whose
   parents are all absent are pruned until nothing changes. When the operation's root capability
   (checkout, cart or order) does not survive, the answer is HTTP 200 with `ucp.status: error` and
   `capabilities_incompatible`.
4. Every response echoes `ucp.version` and the active capabilities relevant to it: checkout
   responses carry checkout and its extensions, carts carry cart, orders carry order.

## Status Machine

| Status | Meaning |
|---|---|
| `incomplete` | An error message blocks completion (unknown or out-of-stock item, missing fulfillment selection, unknown payment handler). |
| `requires_escalation` | The buyer must act in a merchant surface: a `requires_buyer_input` or `requires_buyer_review` message and `continue_url` are present. Without a `continue_url` the session stays `incomplete`. |
| `ready_for_complete` | No error: Complete may be called. |
| `complete_in_progress` | Complete was accepted and the executor is working, or a payment authentication action is outstanding. Update and Complete are refused (409); the response never carries `order`. |
| `completed` | Terminal; carries `order {id, permalink_url}`. |
| `canceled` | Terminal; from any non-terminal state, and when `expires_at` (default 6 hours) passes. |

Totals hold exactly one `subtotal` and one `total` (and `fulfillment` once an option is selected),
signed integers in minor units; the non-total entries add up to the total.

## Idempotency

`Idempotency-Key` records (`commerce_ucp_idempotency`) are kept 48 hours (at least 24 hours by a
database constraint). The same key and the same raw body (SHA-256) replays the stored answer; a
different body or operation is 409 `idempotency_key_reused`; a key whose first request is still
running is 409 `idempotency_key_in_progress`; a storage failure is 503 `idempotency_unavailable`
and nothing runs. A 5xx answer is not stored, so the client can retry.

## Complete: Payment, Arbiter, Executor

A merchant with `provider_policy.payments` (`shopify_cart` or `plural_hosted`) takes payment on its own
page instead: Complete seals and consumes a `payment_handoff` decision and answers `requires_escalation`
with `continue_url`; no executor runs. See [payment-handoff.md](payment-handoff.md). For every other
merchant, in order, each failure being a 200 business outcome with the session unchanged:

1. The session must be `ready_for_complete` (`missing`).
2. Payment (`payment_failed`): exactly one instrument unless split payments is active; the
   handler must be one this merchant offers, the instrument type one it lists, the credential
   well formed for its type and a type the handler accepts (a PAN is refused by tokenised
   handlers), every `ucp.request_constraints` the handler emits satisfied, and the amount within
   the handler's allowance and currency.
3. AP2 (when negotiated): `ap2.checkout_mandate` is required (`mandate_required`).
4. Executor: the merchant's `provider_policy.ucp.executor` must be registered
   (`executor_unavailable`, severity `unrecoverable`). The Plural executor (`plural_pine`)
   is registered only while `PLURAL_PINE_LIVE_EXECUTION_ENABLED=true`
   ([Plural executor](plural-executor.md)).
5. Decision. The arbiter decides action `session_complete` on the session payload: id, revision,
   merchant and seller, release, currency, totals, line items, fulfillment selections and each
   instrument's handler, type and credential SHA-256. A refusal is sealed and returned as
   `arbiter_refused` with the decision under the vendor member `ai.agenticorg.commerce.decision`.
   A caller may instead present a `decision_id` it obtained earlier (`ai.agenticorg.commerce`:
   `{"decide_only": true}` returns an `allow` decision without executing; a second Complete with
   `{"decision_id": "..."}` executes). Either way the decision is consumed through
   `require_allow_decision` (single use, same tenant, same payload hash, unexpired); a different
   credential or a changed session is `decision_payload_mismatch`.
   With a UPI AutoPay instrument the rule `afa_evidence_present` refuses until server-side RBI
   AFA evidence exists. The response then carries the buyer's out-of-band step:
   - an `incomplete` session with the Action `ai.agenticorg.payment.upi_mandate_authorization`
     when the extension `ai.agenticorg.payment.upi_mandate` is negotiated (2026-08-25);
   - otherwise a `requires_escalation` session with the authorization URL as `continue_url`.

   A verified Plural mandate event returns the session to `ready_for_complete`
   ([Mandates](mandates.md#the-out-of-band-step)).
6. The session moves to `complete_in_progress` in the same transaction, then the executor runs
   with the credential in memory only. `completed` creates the order and queues webhooks;
   `action_required` keeps `complete_in_progress` with `actions` (2026-08-25, payment
   authentication negotiated, URL on an allowed origin) or escalates with `continue_url` and a
   `requires_3ds` message (2026-04-08); `failed` returns to `ready_for_complete` with
   `payment_failed`. Two concurrent Completes execute once.

Buyer authority (mandate, consent and Commerce Passport references) is caller-asserted until
verified mandates exist: the vendor member `ai.agenticorg.commerce.buyer_authority` on create,
update or complete, with the same fields as the purchase handoff's `buyer_authority`. With AP2
negotiated the checkout mandate's SHA-256 is the mandate hash (its `exp` read, not yet
verified). Risk signals sent with Complete are recorded as `commerce.ucp.payment.risk_signals`
(keys and SHA-256 only); signals are never echoed.

## Cart (`dev.ucp.shopping.cart`)

- `POST /ucp/carts` is the single call a platform makes at cart transfer; the response's
  `continue_url` is a UCP permalink on the merchant's `permalink_endpoint`
  (`<endpoint>/<item token>:<quantity>,...`, item ids outside the raw token grammar as `~` plus
  base64url).
- `PUT /ucp/carts/{id}` replaces the line items. The body omits `cart.id` (2026-08-25); when a
  body carries it (2026-04-08 marks it required) it must match the path.
- Converting a cart is one call: `POST /ucp/checkout-sessions` with `cart_id`. The cart's line
  items, buyer and context replace the checkout's; an open session for the cart is returned
  instead of a new one (200), and a database constraint keeps one open session per cart.
- Carts have no status; a canceled or expired cart is 404.

## Orders And Webhooks (`dev.ucp.shopping.order`)

- One order per completed session (`commerce_orders`), with line quantities
  `{original, total, fulfilled}` and a derived line status (`processing`, `partial`,
  `fulfilled`, `removed`), fulfillment `expectations` from the selections, append-only `events`
  and `adjustments`, `attribution` and (2026-08-25) `payment.accepted_term`.
- `GET /ucp/orders/{id}` answers the platform that originated the order only.
- Webhooks send the full order snapshot (never a delta) to the platform's
  `dev.ucp.shopping.order` `config.webhook_url`, registered when its checkout completes, and to
  merchant-managed subscriptions. Headers: `Webhook-Id` (stable across retries of one revision),
  `Webhook-Timestamp`, `UCP-Agent: profile="https://<serving host>/.well-known/ucp"`,
  `Content-Digest` (RFC 9530) and an RFC 9421 signature (ES256, raw `r||s`) over `@method
  @authority @path content-digest content-type ucp-agent` with the merchant's active key from the
  merchant key store (the key `/.well-known/ucp` publishes). Without one nothing is sent: the
  delivery retries and then dead-letters.
- Delivery uses an outbox (`commerce_order_webhook_deliveries`) written in the order's
  transaction, a Celery kick after commit and a 30-second sweep; 2xx is delivered, 408/425/429/5xx
  and transport errors retry with exponential backoff, other 4xx or 10 attempts dead-letter.
- Merchant endpoints (tenant administrators):
  `GET/POST /api/v1/commerce/runtime/ucp/order-webhook-subscriptions`,
  `PATCH .../order-webhook-subscriptions/{id}` (enable or disable),
  `POST .../order-webhook-subscriptions/{id}/rotate-key`,
  `GET /api/v1/commerce/runtime/ucp/orders/{id}/webhook-deliveries`,
  `POST /api/v1/commerce/runtime/ucp/order-webhook-deliveries/{id}/replay`, and
  `POST /api/v1/commerce/runtime/ucp/orders/{id}/events` (a fulfillment event or an adjustment;
  sends the new snapshot).
- Order isolation: a platform's own subscription (from its profile) receives only that platform's
  orders and is never shared with another platform that names the same URL (no delivery, logged);
  an `acp_hmac` subscription is bound to one ACP platform's API key (`platform_api_key_id`) and
  receives only that platform's orders; only the merchant's own `ucp` subscriptions receive all.
- A subscription with `scheme: acp_hmac` receives ACP order events (`order_create` /
  `order_update` with the full ACP `Order`) signed with ACP `Merchant-Signature` under its active
  shared key; the key is shown once and stored encrypted with the tenant key. See
  [ACP product feed and checkout sessions](acp-feed-and-sessions.md#order-events-g37). Payload
  renderers stay replaceable (`register_payload_renderer`), and `register_order_event_listener`
  receives every order event in the order's transaction.

UCP has no `order_create`/`order_update` event types: those are ACP's names. The internal
`order.created`/`order.updated` labels only choose the ACP mapping.

## Quantities

`line_items[].quantity` is an integer count of *steps* from 1 to 2^53-1. An item sold other than
each declares `quantity_unit` (`unit` from UN/CEFACT Recommendation 20, never a Recommendation 21
`X*` package code; `scale` 0 to 15, one step being 10^-scale of the unit, `C62` at scale 0;
`increment`). A merchant sets it per item under `provider_policy.ucp.quantity_units`. A platform
that asserts another scale of the same unit is converted visibly (a `quantity_converted`
warning) when exact, otherwise rejected recoverably (`quantity_unit_mismatch`); a different unit
is rejected. Amounts are exact integer arithmetic, rounded half to even. 2026-04-08 has none of
this: its quantity is a plain integer and `quantity_unit` is ignored.

Premise correction: the program described the quantity as `int | Measure`. The published schema
has no such union; `measure.json` is used for `unit_price` and order adjustments only, so the
quantity stays an integer step count.

## Fulfillment

- Methods `shipping` and `pickup` (per the merchant's settings), one group per method.
- Shipping destinations come from the platform (2026-04-08 also accepts `locality`/`region`
  aliases); options are the flat-rate method definitions of the merchant's Shopify delivery
  profiles (read through the Shopify connector, cached five minutes) for the destination country
  and the checkout currency. `free_shipping_min_subtotal_minor_units` zeroes them.
- Pickup destinations are the merchant's enabled offline POS stores: `business_location`
  (2026-08-25) or `retail_location` (2026-04-08), free.
- Business config renders per release: `allows_multi_destination` / `allows_method_combinations`
  (2026-04-08), `multi_destination[]` / `method_combinations` (2026-08-25). Two methods of one type
  need it in `multi_destination`; mixed types need an allowed combination.

## Payment

- Credentials: `card` (2026-04-08 and deprecated in 2026-08-25), `pan`, `network_token` (needs
  `cryptogram`) and handler tokens. Credentials are never stored or echoed; the session keeps the
  instrument's id, handler, type, display data and the credential's SHA-256.
- `ucp.request_constraints` (the program's `$requestConstraints`) are emitted on each available
  instrument of a handler in checkout responses (2026-08-25) and enforced at Complete.
- Payment authentication actions (`dev.ucp.common.payment.device_data_collection`,
  `.three_ds_challenge`) appear in `actions` when an executor asks for them; an action URL must
  be https on an origin the merchant allows (`payment_authentication.allowed_origins`).
- Split payments only when `dev.ucp.common.payment.split_payments` is negotiated: instruments
  carry `amount`, add up to the total and match `allowed_combinations`.
- Payment terms (2026-08-25): `payment.terms` with schedule amounts computed from the total
  (`share_bps` or a fixed `amount`, `due_in_days`), `selected_term_id` on update.
- Token binding passed to the executor: `{type: "dev.ucp.shopping.checkout", id}` (2026-08-25)
  or `{checkout_id}` (2026-04-08). Separately, `ExecutionCheckout.scheme_token_binding` carries the
  scheme token binding of the buyer's payment capability when a verified `mandate_capability`
  artifact holds one (see [purchase and mandate handoff](purchase-mandate-handoff.md#scheme-token-binding)).
- AP2 (`dev.ucp.shopping.ap2_mandate` in 2026-04-08, `dev.ucp.common.payment.ap2_mandate` in
  2026-08-25) is advertised only when a merchant signing key is available; every checkout
  response then carries `ap2.merchant_authorization`, a detached ES256 JWS over the RFC 8785 JSON
  of the checkout without `ap2`.

### Payment handlers

`core/commerce/payment_handlers/` holds the handler interface. The Plural tokenised-instrument
handler (`ai.agenticorg.payment.plural_tokenized`, id `plural_tokenized`) is offered when the
merchant has an enabled `plural_pine` provider and a `provider_policy.ucp.mandate_capability`.
Its config carries that capability (`rails`, `max_amount_minor_units`, `currency`,
`instrument_types`, `evidence: merchant_config`) and the allowance (`max_amount` = the capability
cap). It accepts a network token with cryptogram or a `plural_pine_token`, never a PAN. It moves
no money: executors do. Its config schema is served at the URL the profile advertises,
`https://agenticorg.ai/ucp/handlers/plural-tokenized/2026-10-20/config.json` (with
`instrument.json` beside it), from `ui/public` in the UI image; a regression test maps both URLs
to the files.

## Policies

2026-08-25 checkout, cart and order responses carry `policies[]` mapped from the merchant's
verified OACP `policy` artifact (`map_oacp_policy_to_ucp_policies`), only while that artifact is
a usable cache record; the order keeps the policies of its checkout as a snapshot. 2026-04-08
has no `policies[]`.

## Buyer Consent

`buyer.consent` is the `dev.ucp.consent.*` purpose map in 2026-08-25 (`analytics`, `marketing`
with `marketing.email` and `marketing.sms` segments, `preferences`, `sale_or_sharing`) and four
booleans in 2026-04-08; both are stored as the purpose map. AgenticOrg holds no server-side copy
of the buyer's Grantex consent record, so the map is rendered from the consent the platform sends,
marked `source: platform`; a platform's `source: business` is downgraded.

Premise correction: UCP has no separate JSON-RPC transport. MCP and Embedded are its JSON-RPC
bindings. The handlers above are transport-neutral; the MCP server reaches them through the REST
binding (see MCP Tools).

## Loyalty And Attribution

With `dev.ucp.common.loyalty` negotiated, a merchant loyalty programme
(`provider_policy.ucp.loyalty`), a buyer email and the programme's claim in
`context.eligibility`, the response carries the membership read from the Shopify customer's
metafields (tier and benefits, and a provisional earning forecast). The email is buyer-asserted
until identity linking, so no balance is disclosed, and a lookup failure omits loyalty.
`attribution` (a flat string map) is stored on the session and copied to the order.

## Merchant Settings

```json
{
  "provider_policy": {
    "ucp": {
      "currency": "INR",
      "executor": null,
      "links": [
        {"type": "privacy_policy", "url": "https://merchant.example/privacy"},
        {"type": "terms_of_service", "url": "https://merchant.example/terms"}
      ],
      "fulfillment": {"shipping": true, "pickup": true, "multi_destination": [], "method_combinations": []},
      "loyalty": {"claim": "com.merchant.rewards", "name": "Merchant Rewards", "metafield_namespace": "loyalty",
                  "reward_currency": {"name": "Points", "code": "PTS"}, "earn_points_per_major_unit": 2},
      "mandate_capability": {"rails": ["card", "upi"], "max_amount_minor_units": 50000, "currency": "INR",
                             "instrument_types": ["card"]},
      "permalink_endpoint": "https://merchant.example/buy",
      "quantity_units": {"FABRIC-1": {"unit": "MTR", "scale": 1, "display_text": "metre", "increment": 5}}
    }
  }
}
```

2026-08-25 only: `split_payments.allowed_combinations`, `payment_terms`,
`payment_authentication.allowed_origins`.

## Release Differences Handled

| Area | 2026-04-08 | 2026-08-25 |
|---|---|---|
| AP2 capability name | `dev.ucp.shopping.ap2_mandate` | `dev.ucp.common.payment.ap2_mandate` |
| Quantity | integer | integer step count + `quantity_unit` |
| Destinations | postal address + id; `retail_location` | `shipping_address`; `business_location` |
| Consent | four booleans | purpose map with `source` |
| Card credentials | `card` | `pan`, `network_token` (`card` deprecated) |
| Actions, request constraints, terms, split payments | absent (escalation for 3DS) | present |
| Descriptions (options, schedules) | string | `description.json` object |
| Cart update `id` | required by the schema | omitted |
| Token binding | `{checkout_id}` | `{type, id}` |

## Conformance

The official suite (`Universal-Commerce-Protocol/conformance`, `ucp-sdk` 0.4.6, release
2026-04-08) was run end to end (discovery through the profile route, checkout, order and
webhooks) against a local server (`tests/conformance/ucp_conformance_server.py`): the real
profile route, UCP REST binding, handlers and Postgres, with test doubles for authentication,
the publication state, catalog, rates, buyer authority and a fake executor.

- Suite as published: 1 passed (discovery), 69 failed, 7 skipped. Its platform profile declares
  only `dev.ucp.shopping.order`, so negotiation answers every checkout operation with
  `capabilities_incompatible`.
- Suite with its platform profile also declaring checkout, fulfillment and buyer consent: 54
  passed, 15 failed, 8 skipped in a full run. The two webhook-address tests passed when their
  module was rerun alone (the suite waits two seconds for a webhook), which leaves 13 failures:
  - discount extension not served (4);
  - free shipping for a specific item (1; the minimum-subtotal promotion passes);
  - stored addresses of known customers (3; needs identity linking);
  - `PUT /orders/{id}` adjustments (4; not in either release's REST OpenAPI, which has only
    `GET /orders/{id}`);
  - `UCP-Agent: profile="..."` accepted as valid (1; the spec requires an absolute profile URL,
    so this server answers 400 `invalid_profile_url`).

## Residual Gaps

- The Plural executor is registered only with `PLURAL_PINE_LIVE_EXECUTION_ENABLED=true`; the
  Plural sandbox end-to-end run (`tests/integration/test_plural_executor_sandbox.py`) is pending
  sandbox credentials. Pending payments and completed 3DS challenges are reconciled from verified
  Plural webhooks, with event field names checked only against a fake transport
  ([Plural executor, Residual Gaps](plural-executor.md#residual-gaps)).
- Buyer authority is server-verified only through Grantex mandates (`mandate_request` with
  `AGENTICORG_GRANTEX_MANDATES_ENABLED`); the AP2 `checkout_mandate` itself is not verified.
- The discount extension (`dev.ucp.shopping.discount`) and identity linking are not served.
- The permalink browser endpoint itself is not served by AgenticOrg; carts link to the
  merchant's own `permalink_endpoint`.
- With no active merchant signing key AP2 is not advertised and UCP order webhooks are not sent
  (they wait and dead-letter); the profile itself needs an active key to be served.
- Inbound RFC 9421 request signatures from platforms are not verified here (network identity work).
