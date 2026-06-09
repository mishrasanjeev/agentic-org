# Commerce Agent C6U3 Alias And Refusal Parity Tests

Status: internal test coverage and release-control report only.

This report does not approve production launch, real merchants, public
discovery, checkout/payment creation, live payments, live Plural, provider
calls, merchant private API calls, production config, production allowlists,
cloud resources, protocol publication, external submission, certification,
compliance, conformance, standardization, public-launch readiness, merchant
approval, or production readiness.

## Purpose

C6U3 converts the C6U2 cross-contract parity risks into narrow executable
AgenticOrg tests. The tests prove that AgenticOrg commerce aliases continue to
depend on Grantex-owned commerce contracts, that unsupported buyer actions fail
closed before side effects, and that Grantex errors are translated without
copying private provider, merchant, credential, or raw payload details into
buyer-facing output.

## Tested Parity

| Area | Test coverage | Result |
| --- | --- | --- |
| Alias inventory | `test_c6u3_alias_inventory_is_grantex_only` pins every AgenticOrg commerce alias to a Grantex MCP or REST path. | AgenticOrg commerce tools remain `grantex_commerce:*` aliases only. |
| Payment status passport gate | `test_payment_status_missing_passport_refuses_before_grantex_call` proves missing passport input is refused locally. | `payment_get_status` no longer relies only on Grantex for the first missing-passport refusal. |
| Payment status transport | `test_payment_status_with_passport_uses_grantex_mcp_only` proves status polling calls `/mcp` with `payment.get_status`. | Status polling stays on the Grantex MCP contract. |
| Blocked buyer intents | `test_blocked_buyer_session_intents_never_call_grantex` covers checkout/payment, live provider, merchant private API, fulfillment, and refund requests. | These intents are refused before any Grantex preview call and before any non-Grantex path can exist. |
| Public discovery gate | `test_public_discovery_hides_commerce_agent_tools_when_gate_is_absent` verifies the default public discovery gate hides commerce tools. | Public discovery remains hidden by default. |
| Stale inventory | `test_stale_inventory_is_translated_to_buyer_safe_caution` verifies stale or unknown inventory becomes cautious buyer-safe wording. | AgenticOrg does not promise stock when freshness is missing. |
| Error redaction | `test_grantex_error_translation_redacts_private_details` verifies private URLs, raw payload markers, credentials, and passport-like values are redacted. | Buyer-safe errors do not echo private implementation details. |
| Future channels | `test_channel_targets_are_documented_only_not_live_exposure` verifies future channel labels remain non-enabling response metadata. | ChatGPT, Claude, Gemini, WhatsApp, and Telegram remain future target labels only. |

## Grantex-Only Boundary

The executable alias inventory covers:

- `merchant_get_profile` -> `merchant.get_profile`
- `catalog_search` -> `catalog.search`
- `catalog_get_item` -> `catalog.get_item`
- `inventory_check` -> `inventory.check`
- `cart_create` -> `cart.create`
- `payment_create_intent` -> `payment.create_intent`
- `checkout_create` -> `checkout.create`
- `payment_get_status` -> `payment.get_status`
- `consent_request` -> `/v1/commerce/passports/consent-requests`
- `consent_exchange` -> `/v1/commerce/passports/exchange`
- `buyer_discovery_preview` -> `/v1/commerce/merchants/{merchant_id}/agenticorg-buyer-discovery-preview`

AgenticOrg still must not call payment providers, Plural, provider credential
routes, merchant private APIs, merchant existing systems, or merchant private
connectors directly for commerce.

## Refusal Behavior

| Request class | AgenticOrg result | Grantex call attempted? |
| --- | --- | --- |
| Missing passport for `payment_get_status` | `consent_required` local refusal | No |
| Checkout/payment request in read-only buyer session | `checkout_payment_not_enabled` | No |
| Live provider or live Plural request | `live_provider_not_enabled` | No |
| Merchant private API or merchant existing-system request | `merchant_private_api_not_allowed` | No |
| Fulfillment, shipment, delivery, or tracking execution | `fulfillment_not_enabled` | No |
| Refund, return, replacement, or chargeback execution | `refund_return_not_enabled` | No |
| Public discovery with no explicit AgenticOrg gate | Commerce tools hidden | No public commerce exposure |
| Stale or unknown inventory | Buyer-safe caution | No stock promise |

## Buyer-Safe Error Translation

The C6U3 guardrail keeps Grantex error codes, status, retryability, audit event
references, and decision references when they are safe to show. Error messages
and remediation text are redacted when they contain private URLs, raw payload
markers, credential-like values, passport-like values, database URLs, Redis URLs,
provider credential references, or webhook-secret markers.

## Remaining Gaps

| Gap | Owner | Priority | Next slice |
| --- | --- | --- | --- |
| Source/freshness projection | Grantex + AgenticOrg | P0 | C6U4 buyer-safe catalog, source, freshness, price, tax, warranty, and return projection. |
| Shared public discovery state contract | Grantex + AgenticOrg | P0 | C6U5 cross-repo public discovery state parity. |
| Consent/session/passport revocation propagation | Grantex + AgenticOrg | P0 | C6U6 durable session and revocation propagation. |
| Channel-specific refusal packs | AgenticOrg | P1 | Channel slices after C6U5 and C6U6. |
| schema.org/UCP-style/ACP-style/AP2-style posture | Grantex + AgenticOrg | P2 | C6U10 internal adapter packaging without external publication claims. |
| Order, fulfillment, refund, support, settlement, and payout contracts | Grantex first, AgenticOrg consumer later | P0 | C6U7 and C6U8 post-purchase contracts. |
| AgenticOrg CI/CD cloud-build guard follow-up after PR #723 cancellation | AgenticOrg | P0 | Separate release-control slice, not C6U3. |

## Stop Conditions

- Any AgenticOrg commerce path calls a payment provider, Plural, provider
  credential route, merchant private API, or merchant existing system directly.
- Any buyer-facing output includes raw provider payloads, private merchant URLs,
  secrets, passport/JWT values, database URLs, Redis URLs, production config, or
  production allowlist values.
- Any public A2A, MCP, API, web, ChatGPT, Claude, Gemini, WhatsApp, or Telegram
  metadata exposes commerce tools before explicit Grantex and AgenticOrg
  approval gates exist.
- Any wording claims production launch, public discovery, checkout/payment, live
  provider, live Plural, certification, compliance, conformance,
  standardization, merchant approval, public-launch readiness, or production
  readiness.

## Validation

Expected focused validation:

- `python -m pytest tests/regression/test_commerce_c6u3_alias_refusal_parity.py`
- `python -m pytest tests/unit/test_grantex_commerce_connector.py tests/unit/test_commerce_buyer_session.py tests/unit/test_commerce_buyer_discovery.py tests/unit/test_commerce_sales_agent_guardrails.py`
- `python -m ruff check connectors/commerce/grantex_commerce.py core/commerce/sales_guardrails.py core/commerce/buyer_discovery.py core/commerce/buyer_session.py tests/regression/test_commerce_c6u3_alias_refusal_parity.py`
- `git diff --check origin/main...HEAD`
- Focused secret, private-detail, production config, public discovery, checkout/payment, live provider, direct provider, Plural, merchant private API, and overclaim scans.

This slice adds no runtime API, migration, workflow, dependency, cloud resource,
production config, secret, provider integration, merchant private API path,
production allowlist, public discovery enablement, checkout/payment enablement,
live provider enablement, live Plural enablement, or external protocol
publication/submission action.
