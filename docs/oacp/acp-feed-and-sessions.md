# ACP Product Feed And Checkout Sessions

AgenticOrg renders the Agentic Commerce Protocol (ACP, API version `2026-04-17`) for a merchant:
a product feed built from verified OACP artifacts, and checkout sessions over the same session
table, state engine, arbiter and executor as [UCP checkout](ucp-checkout.md). Both are off by
default.

| Flag | Default | Effect |
| --- | --- | --- |
| `AGENTICORG_ACP_FEED_ENABLED` | `false` | `GET /api/v1/commerce/runtime/acp/feed` answers 404 while off. |
| `AGENTICORG_ACP_CHECKOUT_ENABLED` | `false` | Every `/acp` route answers 404 while off. Separate from `AGENTICORG_UCP_CHECKOUT_ENABLED`, so each protocol is switched on and off on its own. |
| `AGENTICORG_ACP_SIGNATURE_MODE` | `log` | `log` records the `Signature` outcome and never refuses; `enforce` refuses (401 `invalid_signature`) any request a verifier registered for the calling platform does not accept. Any other value is treated as `enforce`. |

A merchant also opts in per protocol: `provider_policy.acp.checkout_enabled: true` in its
commerce config. Without it the merchant's `/acp` routes answer 404 and the feed's
`enable_checkout` column is `false`.

## Product feed

`GET /api/v1/commerce/runtime/acp/feed?merchant_id=<id>&seller_agent_id=<id>&format=<format>`
(tenant administrators). The feed is generated only while the scope holds verified, fresh,
buyer-agnostic `merchant_capability`, `seller_agent_capability`, `catalog_snapshot`, `price` and
`inventory` artifacts: the gate of UCP catalog search. Without them the answer is 409
`feed_unavailable:verified_artifacts_missing:<types>`; no stale feed is produced.

| `format` | Body |
| --- | --- |
| `json` (default) | ACP Feed `ProductsResponse` `{products: [Product]}` (`schema.feed.json`, 2026-04-17) |
| `jsonl` | `products.jsonl`: one `Product` per line, the ACP file-ingestion format |
| `metadata` | `FeedMetadata` `{id, updated_at, target_country?}` (`metadata.json`) |
| `csv` | The merchant-feed columns below, one row per variant |

The `AgenticOrg-Feed-Skipped-Rows` header counts CSV rows left out for a missing required column.

**JSON.** A `Variant` carries `price` and `list_price` in minor units, `availability`
(`available`, `status` `in_stock` / `out_of_stock`), `variant_options`, `categories`, `media`, `url`
and `seller` (`name`; `links` of type `privacy_policy`, `terms_of_service`, `refund_policy`,
`shipping_policy`). `Variant.id` is the item id checkout accepts in `line_items[].id`. The 2026-04-17
feed schema has no member for search or checkout eligibility or for return terms
(`additionalProperties: false`), so returns appear only as the `refund_policy` link. The output is
validated in tests against the vendored schema file
`core/commerce/acp/schemas/2026-04-17/schema.feed.json`.

**CSV.** The flat field list of the merchant product-feed specification (separate from the ACP
repository; the eligibility, seller and returns fields come from it). Rules applied: UTF-8, a
header row, comma-separated with RFC 4180 quoting, booleans `true`/`false`, prices as
`"<major units> <ISO 4217>"` (for example `1499.00 INR`), an empty cell for an unknown optional
value, titles cut to 150 and descriptions to 5,000 characters, and free-text cells starting with
`=`, `+`, `-`, `@`, tab or carriage return prefixed with `'`.

| Column | Source |
| --- | --- |
| `item_id`, `group_id` | Variant id, product id |
| `title`, `description`, `brand`, `product_category` | Synced product (brand falls back to the merchant name) |
| `url` | `provider_policy.acp.feed.product_url_template` (`https`, with `{item_id}`), else a UCP permalink on the merchant's `permalink_endpoint` |
| `image_url` | The variant's or product's first image |
| `price`, `sale_price` | `list_price` and `price` when a compare-at price exists, else `price` alone |
| `availability` | `in_stock`, `out_of_stock`, or `unknown` without an inventory snapshot |
| `seller_name`, `seller_url` | Merchant display name; the attested serving host |
| `seller_privacy_policy`, `seller_tos` | Merchant `privacy_policy` and `terms_of_service` links |
| `accepts_returns`, `return_deadline_in_days`, `return_policy` | The verified OACP `policy` artifact only (`accepts_returns`, `return_window_days`, `return_policy_url`); blank otherwise |
| `enable_search` | `provider_policy.acp.feed.enable_search` (default `true`) |
| `enable_checkout` | `true` only with search on, `AGENTICORG_ACP_CHECKOUT_ENABLED` on, the merchant opted in, and both policy links known |

Required columns: `item_id`, `title`, `description`, `url`, `brand`, `seller_name`, `image_url`,
`availability`, `price`. Optional settings: `provider_policy.acp.feed.target_country` (ISO 3166-1
alpha-2) appears in `FeedMetadata`.

The ACP Feed API itself is a push API the agent platform hosts (`POST /feeds`,
`PATCH /feeds/{id}/products`). AgenticOrg produces the files and payloads; it does not push them.

## Checkout sessions

At `https://<merchant serving host>/acp`, authenticated with `Authorization: Bearer` and the tenant
API key the merchant issued to the agent platform (scopes `acp:read`, `acp:write`; no role holds
them). The merchant is resolved from `Host` exactly as for UCP: an attested, published serving
host inside the tenant of the credential.

| Operation | Route | Success |
| --- | --- | --- |
| Create | `POST /acp/checkout_sessions` | 201 `CheckoutSession` |
| Update (partial) | `POST /acp/checkout_sessions/{id}` | 200 |
| Get | `GET /acp/checkout_sessions/{id}` | 200 |
| Complete | `POST /acp/checkout_sessions/{id}/complete` | 200 `CheckoutSessionWithOrder` once completed |
| Cancel | `POST /acp/checkout_sessions/{id}/cancel` | 200; 405 once it cannot be canceled |

### One state machine

An ACP request is translated into the UCP checkout request and run by the UCP operations
(`core.commerce.ucp.handlers`) on the same `commerce_checkout_sessions` rows. Status mapping:

| Session state | ACP `status` |
| --- | --- |
| `incomplete` | `not_ready_for_payment` |
| `ready_for_complete` | `ready_for_payment`, or `not_ready_for_payment` while an ACP check (currency, delegate-payment allowance) blocks it |
| `requires_escalation` | `requires_escalation` with `continue_url` |
| `complete_in_progress` | `complete_in_progress` |
| `completed` | `completed` with `order {id, checkout_session_id, permalink_url, status}` |
| `canceled` | `canceled` |
| past `expires_at` | `expired` |

Sessions are tagged `protocol: acp` and owned by the API key that created them: a UCP route never
sees an ACP session, and another platform's key gets 404. Writes to a session that can no longer
change (completed, canceled, expired, executing) are 405 `session_not_modifiable`.

Translation:

- **Line items.** ACP 2026-04-17 `Item` is `{id, name?, unit_amount?}` with no quantity. One entry is
  one unit; a repeated `id` adds a unit to the same line. A `unit_amount` that differs from the
  merchant's price adds a `price_change` warning.
- **Update** is partial: members present replace the stored ACP input, and the merged input runs as
  a full replacement under the session lock. Line ids stay stable across updates.
- **Fulfillment.** `fulfillment_details.address` is the shipping destination; a `shipping` entry in
  `selected_fulfillment_options` (an update member) selects the rate. Pickup is not offered over ACP
  (`FulfillmentOptionPickup` needs a full store address the merchant config does not hold).
- **Payment.** `payment_data {handler_id, instrument {type, credential}}` is the one instrument;
  `purchase_order_number` payment is not supported. `risk_signals` are sealed on the ledger as keys
  and a SHA-256 (`commerce.acp.payment.risk_signals`).
- **Currency.** The request `currency` must match the items' currency; otherwise an `invalid`
  message on `$.currency` blocks payment.

Completion requires an `allow` decision of the commerce arbiter (action `session_complete`) bound to
the session payload hash, consumed by a registered executor, exactly as UCP Complete does. No
executor is registered in this release, so Complete answers an `unsupported` message and the session
stays `ready_for_payment`.

### Headers

| Header | Rule |
| --- | --- |
| `API-Version` | Required; `2026-04-17`. Missing: 400 `missing_api_version`; another value: 400 `unsupported_api_version`; both carry `supported_versions`. |
| `Idempotency-Key` | Required on every POST (1 to 255 characters): missing is 400 `idempotency_key_required`. The same key and body replays the stored status and body with `Idempotent-Replayed: true`; a different body is 422 `idempotency_conflict`; a request still running is 409 `idempotency_in_flight` with `Retry-After: 1`. Bodies are compared as canonical JSON (key order ignored, array order and `null` kept). A 5xx answer is not stored. Records live 48 hours. The key is echoed. |
| `Request-Id` | Echoed; generated when absent. |
| `Signature`, `Timestamp` | See below. |

A key is scoped to the merchant: reusing it on another ACP path is 422 (stricter than the spec's
per-path scope).

### Request signatures (residual)

ACP recommends `Signature` and `Timestamp` but publishes no algorithm, canonical form, signed fields
or key distribution: they are arranged out of band with each agent platform. Verification is
therefore pluggable: `core.commerce.acp.headers.register_signature_verifier(platform, verifier)`
registers a verifier for one platform (`apikey:<prefix>` of its API key), checked with a 300 second
`Timestamp` window. No verifier ships. In the default `log` mode the outcome (`absent`,
`no_verifier`, `stale_timestamp`, `invalid`, `verified`) is logged and nothing is refused; `enforce`
refuses everything a registered verifier does not accept, which today is every request.

### Errors (G40)

Protocol errors are ACP `Error` objects `{type, code, message, param?}` with `type`
`invalid_request`, `processing_error` or `service_unavailable`; schema violations of the request are
400 `invalid` with the JSONPath in `param`. Business outcomes are `messages[]` on a valid session.
`core.commerce.acp.errors` maps every internal code (UCP engine, completion, decision consumption,
the ACP checks) to the closed `MessageError` code enum, warnings ACP does not define become info
messages, and UCP severities become `resolution`. Examples:

| Internal code | ACP code |
| --- | --- |
| `out_of_stock`, `not_found`, `missing` | same |
| `item_unavailable`, `invalid_quantity`, `invalid_option`, `currency_mismatch` | `invalid` |
| `address_undeliverable` | `region_restricted` |
| `payment_failed`, `arbiter_refused`, `decision_not_allow` | `payment_declined` |
| `executor_unavailable`, `payment_handler_unavailable` | `unsupported` |
| `decision_payload_mismatch`, `decision_consumed` | `conflict` |
| `delegate_payment_allowance_exceeded` | `maximum_exceeded` |

Internal blocker dicts and UCP envelopes never appear in an ACP response.

### Capabilities, extensions, discounts (G38)

The session's `capabilities` declares only what is served:

- `payment.handlers`: the merchant's handlers that have an ACP projection (today the Plural
  tokenised instrument, below).
- `extensions`: the agent's requested extensions that are served. The vendor extension
  `ai.agenticorg.commerce` adds `agenticorg_commerce` to the create, update and complete requests and
  to the session: `buyer_authority` references, `decision_id` or `decide_only` for the arbiter, and
  `passport_jwt` (the buyer's Commerce Passport). The session echoes the arbiter decision there.
- The core `discount` extension is not served: it is never declared, no `discounts` member is
  rendered, and request discount codes are ignored.
- No intervention (3DS, biometric, address verification) is served, so `interventions` is omitted.

### Payment handler

`ai.agenticorg.payment.plural_tokenized` (id `plural_tokenized`, version `2026-10-20`):
`requires_delegate_payment: true`, `requires_pci_compliance: false`, `psp: plural_pine`. Its config
schema and instrument schema are served from agenticorg.ai
(`/ucp/handlers/plural-tokenized/2026-10-20/config.json` and `instrument.json`, files in
`ui/public`). The agent presents a Plural instrument token (`credential.type: plural_pine_token`,
for example the vault token from delegate payment) or a network token; a card number is refused.

### Delegate-payment allowance (G35)

When the vendor extension carries `passport_jwt` on create or update, AgenticOrg asks Grantex
`POST /v1/commerce/oacp/acp/delegate-payment-allowance` for the session's allowance: the service
token, async over the DNS-pinned egress transport, 5 second timeout, no retry, no card data. The
answer appears in the handler's `config.delegate_payment` in the `DelegatePaymentRequest` shape
(`allowance`, `risk_signals`, `metadata`); the agent adds `payment_method` and sends it to the
vault. The passport is kept only as a SHA-256; each request is sealed on the ledger
(`commerce.acp.delegate_payment.allowance`, with the risk-signal digest and Grantex's audit event id).

Grantex unreachable, slow or answering 5xx gives a `missing` message at once (update the session to
retry); a Grantex refusal gives `payment_declined`; no passport gives `missing` on
`$.agenticorg_commerce.passport_jwt`. Completion needs an issued allowance in the session currency,
not expired, covering the total (`maximum_exceeded` otherwise).

### Order events (G37)

Orders created from ACP sessions are the UCP order entity. A merchant subscribes an ACP agent
platform's receiver with an `acp_hmac` webhook subscription
(`POST /api/v1/commerce/runtime/ucp/order-webhook-subscriptions`, `scheme: acp_hmac`; the shared
secret is shown once). Each order revision is delivered as
`{type: order_create | order_update, data: <full ACP Order>}` with
`Merchant-Signature: t=<unix>,v1=hex(HMAC-SHA256(secret, t + "." + body))`, `Request-Id` (stable per
revision) and `Timestamp`, through the UCP outbox (retries, dead letters, replay). Signing uses the
subscription's active key only (one `v1` per header): after a key rotation the receiver switches
secret at once. Merchant subscriptions receive all of the merchant's orders. The signature matches
the shared vectors in `tests/fixtures/acp/merchant-signature-v1.json`.

### Ledger events

`commerce.acp.checkout.create`, `.update`, `.complete`, `.cancel`, `commerce.acp.checkout.execution`,
`commerce.acp.order.created`, `commerce.acp.payment.risk_signals`,
`commerce.acp.delegate_payment.allowance`, plus the arbiter's decision events. A refusal answered
before any business write (headers, schema, unknown host) is recorded with outcome `refused`.

## Premise corrections

- ACP 2026-04-17 create takes `line_items: [{id, name?, unit_amount?}]` with no quantity; the
  program's `{id, quantity}` is the 2026-01-16 shape. This server follows the 2026-04-17 schema.
- `enable_checkout`, `enable_search` and return terms come from the merchant product-feed
  specification, not from the ACP feed schema; they appear in the CSV only.
- ACP order events are `order_create` / `order_update`, not dotted `order.*` names.
- ACP request signing has no published algorithm: verification is per platform and log-only by
  default.

## Rollback

Set `AGENTICORG_ACP_CHECKOUT_ENABLED=false` (every `/acp` route answers 404; sessions, orders and
undelivered webhooks are kept) and `AGENTICORG_ACP_FEED_ENABLED=false`. For one merchant, set
`provider_policy.acp.checkout_enabled` to false; for one platform, revoke its API key; for one
receiver, disable its `acp_hmac` subscription.
