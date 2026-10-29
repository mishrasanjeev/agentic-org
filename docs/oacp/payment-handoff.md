# Buyer payment hand-off

AgenticOrg never collects money for a merchant. When a buyer's checkout is
ready, Complete hands the buyer a link to the merchant's own payment page and
the buyer pays there. This page describes the configuration, the decision, the
routes and how the merchant learns about the order.

Code: `core/commerce/payment_handoff.py` (settings, the Shopify cart permalink,
the decision payload), `core/commerce/plural_hosted.py` (Plural credentials,
hosted orders, the webhook and the status poll), the hand-off branch of
`complete_checkout` in `core/commerce/ucp/handlers.py`, and
`api/v1/commerce_payments.py` (routes).

## Configuration

A merchant chooses a mode in its commerce config, `provider_policy.payments`:

```json
{"mode": "shopify_cart"}
{"mode": "plural_hosted", "plural": {"credential_ref": "commerce_plural_<digest>"}}
```

Without `provider_policy.payments` nothing changes: Complete keeps the
agent-executed path (a registered executor, `session_complete`, mandates). The
setting is per merchant, so no deployment flag or environment change is needed
to turn a merchant on. `PUT /api/v1/commerce/runtime/merchant-configs/{merchant_id}/payments`
saves a mode only when it can work: a connected Shopify store for
`shopify_cart`, saved Plural credentials with a webhook secret for
`plural_hosted`. `off` removes the setting.

| Mode | Where the buyer pays | Who confirms the order |
| --- | --- | --- |
| `shopify_cart` | `https://<shop>.myshopify.com/cart/<variant id>:<qty>,...`, the store's own cart permalink | Shopify; the order is in the merchant's Shopify admin |
| `plural_hosted` | The Plural hosted checkout page of the merchant's own Plural account | Plural's signed webhook (or the status poll); the order is created here |

The Shopify variant ids stay server-side. Public item ids are the redacted UCP
ids (`shopify_variant:<sha>:redacted`); each checkout line keeps the numeric
part of the Shopify variant gid from the synced product evidence as
`storefront_variant`, which is never rendered. Only the buyer's link carries it.
The store domain is the merchant's enabled Shopify source connector.

## The decision

Complete seals an arbiter decision for the action `payment_handoff` over the
binding payload `handoff_payload` (session, revision, merchant, seller, mode,
currency, totals and the RFC 8785 hash of the lines, totals and fulfillment). It
evaluates two rules:

- `oacp_artifacts_valid` for the catalog, price, inventory, policy and protocol
  adapter families (not `mandate_capability`: no agent-held instrument is charged);
- `handoff_merchant_limits`: the merchant's `provider_policy.arbiter` limits when
  they are configured; without them it passes.

`buyer_authority_verified`, `mandate_present_unexpired`, `consent_active` and
`passport_amount_cap` are not evaluated. The agent moves no money and holds no
payment credential, and the buyer authenticates and pays on the payment page
(3-D Secure, UPI PIN), so there is nothing for a mandate to authorise. The
decision is offered to Grantex for countersignature after its transaction
commits, and the allow is consumed (single use, exact payload hash) before any
link exists; where `AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT` is on, an allow
without the Grantex grant is refused (`grantex_grant_missing`), as for every
other action. Migration `v6z104_payment_handoff_action` widens the action check
constraint.

## What Complete answers

A ready checkout answers `requires_escalation` with `continue_url` (the link)
and one message:

```json
{"type": "error", "code": "payment_handoff", "severity": "requires_buyer_input",
 "content": "Open continue_url to pay for this order in the shop's own Shopify checkout.", "path": "$.payment"}
```

Repeating Complete returns the same link while it is valid (a Shopify link for
the session's lifetime, a Plural link for 30 minutes); after that, a new
decision and a new link. Payment instruments, decision ids and mandate requests
in the body are ignored. ACP sessions run through the same handler and render
`continue_url` the same way.

| Business message | Meaning |
| --- | --- |
| `payment_setup_incomplete` | No connected Shopify store, or no usable Plural credentials. |
| `payment_handoff_unavailable` | The items are not Shopify variants (cart mode), or the binding allows only a hand-off and the merchant has none. |
| `payment_link_unavailable`, `payment_link_refused` | Plural could not be reached or refused the order; the session is ready again. |
| `arbiter_refused` | The decision refused (for example a stale artifact). |

## Plural hosted checkout

Each merchant saves its own Plural client id, client secret and webhook secret
(`PUT .../payments/plural-credentials`). They are encrypted with the tenant key
(`encrypt_for_tenant`) into a `connector_configs` row named
`commerce_plural_<sha256(merchant_id)[:24]>`, which is the `credential_ref`.
They are never returned, logged or ledgered. `POST .../plural-credentials/test`
fetches an access token and nothing else. The environment (`sandbox` or
`production`) fixes the API base URL.

The hosted order:

```
POST {base}/checkout/v1/orders
{"merchant_order_reference": "aoh-<sha256(session|decision)[:44]>",
 "order_amount": {"value": <total minor units>, "currency": "INR"},
 "pre_auth": false,
 "allowed_payment_methods": ["CARD", "UPI", "NETBANKING"],
 "callback_url": "<public API>/api/v1/payments/plural/return/<tenant>/<checkout>",
 "failure_callback_url": "<same>",
 "purchase_details": {"customer": {...as the buyer gave it...},
                      "merchant_metadata": {"oacp_decision_id": ..., "oacp_checkout_session_id": ..., "oacp_payload_hash": ...}}}
```

Plural answers `order_id`, `redirect_url`, `token` and `response_code`; the
`redirect_url` is the buyer's link. The order is created outside any database
transaction and never retried. While it is being created the session is
`complete_in_progress`; a reservation that never finishes is released by the
poll after five minutes. While a Plural link is out the checkout cannot be
updated (409); cancel and open a new one to change it.

Confirmation:

- **Webhook**: `POST /api/v1/payments/plural/webhooks/{tenant_id}/{webhook_key}`,
  the URL the merchant registers in its Plural dashboard (shown on the payment
  settings). The body is verified with the merchant's webhook secret
  (HMAC-SHA256 over `webhook-id.webhook-timestamp.body`, five-minute window); a
  `webhook-id` already applied to the session is a no-op. An event without the
  amount is completed from `GET {base}/pay/v1/orders/{order_id}`.
- **Status poll**: the beat task `poll-plural-handoffs` (every 60 s, while UCP or
  ACP checkout is on) reads the status of every open Plural hand-off once.
- **Return page**: `GET`/`POST /api/v1/payments/plural/return/{tenant_id}/{checkout_id}`
  reads the status from Plural and shows the buyer a plain result. It shows no
  buyer data and ignores the posted fields.

`PROCESSED` or `CAPTURED` with the amount and currency the hand-off asked for
creates the order (as an executed Complete would: the order entity with
`executor` `plural_hosted`, the ledger, the UCP order webhooks). A payment for
another amount, on a canceled checkout or for a checkout that changed is
refused and logged as `plural_hosted_payment_unmatched`, so the merchant can
refund it in Plural. `FAILED`, `CANCELLED` and the like retire the link; the
next Complete issues a new one. `AUTHORIZED` is not money received and stays
pending.

## The merchant gets the order

- An email to every active administrator of the tenant (`core/commerce/order_notifications.py`,
  sent from a Celery task after the order commits) with the order, items, total,
  the buyer contact as given and the surface the buyer used.
- `GET /api/v1/commerce/runtime/orders?merchant_id=&limit=&offset=`: the tenant's
  orders, newest first, and the Orders page in the console.
- The existing UCP order webhooks to the platform that placed the order.

Orders paid in a Shopify cart are confirmed by Shopify and appear in the
merchant's Shopify admin, not here.

## Network surfaces

`POST /api/v1/network/shops/{slug}/checkout-sessions/{id}/complete` is the
hand-off only: the gateway refuses any body that carries a payment instrument,
decision, mandate or AP2 mandate, and the handler refuses (business message
`payment_handoff_unavailable`) a merchant without a hand-off instead of running
an executor. The MCP network mode's `complete_checkout` tool and the Telegram
bot's `get_payment_link` tool call it and pass the buyer `continue_url`.
