# Google Merchant Center UCP Onboarding Runbook

Canonical runtime docs: [UCP profile](../oacp/ucp-profile.md), [UCP checkout](../oacp/ucp-checkout.md)
and [OACP end-user flow](../oacp/end-user-flow.md).

This runbook takes a merchant already onboarded on AgenticOrg (Shopify source, Grantex
authority, verified artifacts) to the Google surfaces that buy through UCP: AI Mode in Google
Search and the Gemini app. It records only facts from Google's public UCP documentation (see
[Sources](#sources)). Where Google's pages are silent, this runbook says so and does not guess.

## What Google Targets

| Item | Google public docs | AgenticOrg |
| --- | --- | --- |
| UCP release | `2026-04-08`. The implementation pages are versioned `2026-04-08`, and the live platform profile `https://ucp.goog/.well-known/ucp.json` declares `2026-04-08`. Google also serves a `2026-08-25` profile. | `AGENTICORG_UCP_VERSIONS=2026-04-08` (the default) serves exactly that release; adding `2026-08-25` publishes it as well. |
| Business profile | `/.well-known/ucp` on the merchant's host | Served on the attested serving host while `AGENTICORG_UCP_PROFILE_ENABLED=true` and the merchant is published |
| Cart transfer | `POST /carts`, called once with all accumulated line items; the response `continue_url` must lead to a page on the merchant's site | `POST /ucp/carts`; `continue_url` is a UCP permalink on the merchant's `permalink_endpoint` |
| Native checkout | `POST /checkout-sessions`, `GET`, `PUT`, `/complete`, `/cancel`; requires `dev.ucp.shopping.checkout`, `dev.ucp.shopping.fulfillment` and the Google Pay payment handler `com.google.pay` | Checkout and fulfillment are served. AgenticOrg offers the Plural tokenised handler, not `com.google.pay`, so **native checkout through Google Pay is not available**. Cart transfer is the supported path. |
| Order updates | Order webhooks to the `webhook_url` in Google's `dev.ucp.shopping.order` profile entry | Full order snapshots to the platform's `config.webhook_url` (RFC 9421 signed) |
| Links | `privacy_policy` and `terms_of_service` are required | `provider_policy.ucp.links` |
| Authentication | Only `Authorization: Bearer` is documented | A tenant API key the merchant issues to Google, as a bearer token, scope `ucp:read` and `ucp:write` |
| Identity linking | A Merchant Center path of its own | Not served (residual) |

## Prerequisites (AgenticOrg)

1. **Merchant onboarded** per the [Shopify runbook](oacp-shopify-merchant-onboarding.md):
   - read-only sync done;
   - Grantex authority requested;
   - verified, fresh `catalog_snapshot`, `price` and `inventory` artifacts cached.
2. **UCP profile published** ([UCP profile, Operator flow](../oacp/ucp-profile.md#operator-flow)):
   - serving host attested by DNS TXT;
   - signing key present;
   - `publish` recorded;
   - an enabled `public_discovery_state` artifact cached.
3. **Merchant settings** under `provider_policy.ucp`:
   - `links` with `privacy_policy` and `terms_of_service` (https);
   - `permalink_endpoint` (an https page on the merchant's site that implements the UCP
     permalink, for the cart `continue_url`);
   - `fulfillment` (shipping and/or pickup);
   - `currency` for items whose source carries none.
4. **Flags in the target environment:**
   - `AGENTICORG_UCP_PROFILE_ENABLED=true`
   - `AGENTICORG_UCP_CHECKOUT_ENABLED=true`
   - `AGENTICORG_UCP_VERSIONS=2026-04-08`
5. **A tenant API key for the platform**, with `ucp:read` and `ucp:write`, stored in the
   merchant's secret manager. It is shared with Google only through Merchant Center.

## Verification Before Merchant Center

Run these against the serving host, never against an API hostname.

| Check | Expected |
| --- | --- |
| `GET https://<host>/.well-known/ucp` | 200, `ucp.version: 2026-04-08`, `dev.ucp.shopping` over `rest` with endpoint `https://<host>/ucp`, capabilities including `dev.ucp.shopping.cart`, `dev.ucp.shopping.checkout`, `dev.ucp.shopping.fulfillment` and `dev.ucp.shopping.order` |
| `POST https://<host>/ucp/catalog/lookup` with a known SKU | 200 with the product; prices in minor units |
| `POST https://<host>/ucp/carts` with `UCP-Agent: profile="https://ucp.goog/.well-known/ucp.json"`, the bearer key and two line items | 201; `continue_url` starts with the merchant's `permalink_endpoint` |
| The same request with the same `Idempotency-Key` | The same cart |
| Commerce ledger (`GET /api/v1/commerce/runtime/ledger/events`) | A `commerce.ucp.cart.create` row for the tenant |

The negotiation fetches Google's platform profile over https from a public address. A host
that cannot reach `ucp.goog` returns `424 profile_unreachable`.

## Merchant Center Steps

Follow Google's guides in this order. Google may change labels, so the guide governs.

1. **Register interest.** Google gates the programme with an interest form. Use the merchant's
   Merchant Center account.
2. **Publish the profile.** Give Merchant Center the merchant's UCP profile
   (`https://<host>/.well-known/ucp`), per Google's "publish profile" page for `2026-04-08`.
3. **Choose the path.** Choose **cart transfer**. Do not enable native checkout until a Google
   Pay handler exists (see the table above).
4. **Credentials.** Give Google the bearer credential through the mechanism Google's guide
   names. Never send it by email or paste it into a ticket.
5. **Sandbox validation.** Google validates in a sandbox before production. Keep the AgenticOrg
   staging host for this step. Watch the ledger for `commerce.ucp.cart.create` rows and for
   `profile_unreachable` or `capabilities_incompatible` outcomes.
6. **Production.** Repeat the verification table against the production host. Then confirm in
   Merchant Center.

Product data in Merchant Center (offers and their ids) is managed by the merchant's existing
Merchant Center feed. Google's UCP pages read for this runbook do not say how feed item ids
must relate to UCP item ids. AgenticOrg accepts its catalog's UCP variant id, the SKU, the
source variant id and the numeric part of a Shopify variant gid as `item.id`. Confirm the
mapping in Google's sandbox before production.

## Rollback

| Scope | Action |
| --- | --- |
| One merchant, transactions | Revoke the API key issued to Google. Every `/ucp` transaction call is then refused while the profile stays up. |
| One merchant, discovery | `POST /api/v1/commerce/runtime/merchant-configs/{merchant_id}/ucp/unpublish`. Cached profiles expire within 5 minutes. |
| All merchants | `AGENTICORG_UCP_CHECKOUT_ENABLED=false` (transaction routes return 404) and, if needed, `AGENTICORG_UCP_PROFILE_ENABLED=false` (profile, catalog and location return 404). |
| Merchant Center | Disable the programme for the merchant in Merchant Center, following Google's guide. |

Stored carts, sessions, orders and ledger rows are kept.

## Residual Gaps

- **Native checkout.** Not available with Google Pay: no `com.google.pay` handler is offered.
- **Identity linking** (`dev.ucp.common.identity_linking`) is not served.
- **Discount** (`dev.ucp.shopping.discount`) is not served.
- **Message signatures.** Google's pages read here do not say whether Google requires HTTP
  message signatures on merchant responses or webhooks. AgenticOrg signs order webhooks (RFC
  9421) regardless.
- **Status names.** Google's overview page lists checkout statuses `ready_for_payment` and
  `error`, which are not in the UCP status enum. AgenticOrg implements the UCP enum.

## Sources

Google public documentation (read 2026-10-19):

- `https://developers.google.com/merchant/ucp/guides/tools/merchant-center/overview`
- `https://developers.google.com/merchant/ucp/guides/overview/ucp-profile`
- `https://developers.google.com/merchant/ucp/guides/overview/native-checkout`
- `https://developers.google.com/merchant/ucp/implementation/2026-04-08/publish-profile`
- `https://developers.google.com/merchant/ucp/implementation/2026-04-08/native-checkout-api`
- `https://developers.google.com/merchant/ucp/implementation/2026-04-08/cart-api`
- Google's platform profiles `https://ucp.goog/.well-known/ucp.json` and
  `https://ucp.goog/.well-known/2026-08-25/ucp.json`
