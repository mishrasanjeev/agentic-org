# UCP Buyer Connector

The `ucp_buyer` connector lets an AgenticOrg agent shop at a public UCP merchant as a buyer: it
searches the catalog, opens a checkout session, reads it and updates it. It stops before payment.
It has no tool that completes a checkout, cancels one or sends a payment instrument, so it cannot
move money. The buyer finishes the purchase with the merchant, for example at the session's
`continue_url`.

The merchant side of the same protocol is described in [UCP business profile, catalog and
locations](ucp-profile.md) and [UCP checkout, cart and order](ucp-checkout.md).

## Configuration

| Field | Kind | Required | Meaning |
|---|---|---|---|
| `merchant_profile_url` | config | yes | The merchant's UCP profile, an https URL ending in `/.well-known/ucp` (for example `https://merchant.example/.well-known/ucp`). |
| `agent_profile_url` | config | for checkout tools | The buyer platform's own public UCP profile URL. It is sent as `UCP-Agent: profile="<url>"`; the merchant fetches it and negotiates checkout against it, so it must declare `dev.ucp.shopping.checkout` at `2026-04-08`. |
| `api_key` | credential (secret) | for checkout tools | The platform API key the merchant issued to this platform (`ao_sk_...` on an AgenticOrg merchant), with the scopes `ucp:read` and `ucp:write`. Sent as `X-API-Key` on checkout calls only. It is stored encrypted like every connector credential and is never logged or returned in a tool result. |

## How it finds the merchant

1. The connector fetches `merchant_profile_url` with no credentials: https only, a public address
   checked before the request, no redirects, a 256 KiB body limit and the connector timeout. The
   profile is kept for five minutes.
2. It takes the REST endpoint of `ucp.services["dev.ucp.shopping"]` with `transport: "rest"` and
   `version: "2026-04-08"`.
3. The endpoint must be on the same origin (scheme, host and port) as the profile URL. Otherwise
   nothing more is sent and the tool returns `merchant_endpoint_origin_mismatch`, so the API key
   only ever goes to the host the operator configured.
4. A tool whose capability the profile does not list at `2026-04-08` (catalog search, catalog
   lookup, checkout) returns `capability_not_offered` without calling the merchant.

## Tools

| Tool | Request | Notes |
|---|---|---|
| `catalog_search(query, currency="INR", limit?, cursor?)` | `POST {endpoint}/catalog/search` with `{"query", "context": {"currency"}, "pagination"?}` | Public, no API key. Returns per product its id, title and plain description, and per variant its id, SKU, title, price (amount and currency) and availability status. The merchant's `messages[]` are kept, for example `catalog_unavailable`. |
| `catalog_lookup(ids)` | `POST {endpoint}/catalog/lookup` with `{"ids": [...]}` | Public. Ids may be product ids, variant ids or SKUs (1 to 50). |
| `checkout_create(line_items, buyer?)` | `POST {endpoint}/checkout-sessions` with `{"line_items": [{"item": {"id"}, "quantity"}], "buyer"?}` | `line_items` is `[{item_id, quantity}]`, where `item_id` is a variant id from the catalog. |
| `checkout_get(checkout_id)` | `GET {endpoint}/checkout-sessions/{id}` | |
| `checkout_update(checkout_id, checkout)` | `PUT {endpoint}/checkout-sessions/{id}` | Full replacement, as the UCP REST binding defines it: send every line item, the buyer and the fulfillment selection to keep. Only `line_items`, `buyer`, `context` and `fulfillment` are accepted; `payment`, `ap2` and the `ai.agenticorg.commerce` member are refused with `payment_not_available`. |

Checkout calls send `X-API-Key`, `UCP-Agent` and a new `Request-Id`; create and update also send a
new `Idempotency-Key`. Checkout results carry the session `id`, `status`, `currency`, line items,
`totals`, `messages`, `continue_url` and `expires_at` when present, `payment: "not_available"` and
a fixed note that payment and completion are not available to this agent. Amounts are integer
minor units of the currency (89900 INR is 899.00 INR).

Errors come back as `{"error", "message", "retryable", "http_status"?, "merchant_code"?,
"merchant_message"?}`: `merchant_auth_rejected` (401 or 403), `not_found` (404: an unknown session,
or the merchant does not serve checkout), `conflict` (409), `negotiation_failed` (422 or 424),
`rate_limited` (429), `merchant_error` (5xx), `merchant_unreachable`, `merchant_timeout`,
`merchant_response_too_large`, `merchant_refused` (an HTTP 200 UCP error response) and
`invalid_request` (a bad argument, refused before anything is sent).

## Agent tool names

`authorized_tools` names the tools connector-qualified, because `catalog_search` and
`checkout_create` are also tool names of the `grantex_commerce` connector, which keeps the bare
names:

```
ucp_buyer:catalog_search
ucp_buyer:catalog_lookup
ucp_buyer:checkout_create
ucp_buyer:checkout_get
ucp_buyer:checkout_update
```

## Governance

The action taxonomy (`core/governance/action_policy.py`) classes these tools by their qualified
name, before any bare tool name: `catalog_search`, `catalog_lookup` and `checkout_get` are reads;
`checkout_create` and `checkout_update` are customer-facing writes (`customer-write`), not money. A
UCP checkout session is a priced draft and only Complete executes payment. The bare
`checkout_create` (and `grantex_commerce:checkout_create`, which creates a payment handoff) stays
`money`.

Reads dispatch in every runtime. Customer-facing writes are contained like every other unsafe
action: in strict runtimes (production, staging, preview) they run in shadow and are not sent; in
other runtimes they need the live capability flag `safety.live_capability.<domain>.customer_write`
and a matching capability authorization.
