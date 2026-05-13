# Commerce Agent Internal Sandbox

AgenticOrg Commerce V1 is scoped to the agent and workflow layer. It depends on
the Grantex Commerce V1 internal-sandbox control plane from Grantex draft PR
#326 and must use Grantex Commerce REST/MCP only.

## Configuration

Set the Grantex target through connector config or environment:

- `base_url` on the `grantex_commerce` connector config, or
- `GRANTEX_COMMERCE_BASE_URL`, falling back to `GRANTEX_BASE_URL`.

Authentication is passed as a bearer token or agent assertion in connector
config (`bearer_token`, `agent_assertion`, `access_token`, `token`, or
`api_key`) or through the matching Grantex environment variables. AgenticOrg
does not store or handle payment provider credentials for this flow.

## Scope

- Internal sandbox only.
- No production deployment change.
- No live Plural, Pine Labs, Stripe, or other direct payment provider calls.
- No local Commerce Passport minting. Passports are only Grantex outputs after
  a successful consent exchange.
- Payment status polling goes through Grantex Commerce only.

## Local Demo Flow

1. Discover products with `catalog_search`.
2. Answer Q&A from `catalog_get_item`, `merchant_get_profile`, and
   `inventory_check` data.
3. Create a cart draft with `cart_create`.
4. Request checkout consent with `consent_request`.
5. Exchange granted consent with `consent_exchange`.
6. Create a payment intent with `payment_create_intent`.
7. Create checkout handoff with `checkout_create`.
8. Poll status with `payment_get_status`.

The agent must refuse checkout/payment without granted consent and a valid
Grantex Commerce Passport, must respect amount caps and policy denials, and
must avoid unsupported claims about EMI, offers, discounts, warranty, tax,
return policy, or inventory.

