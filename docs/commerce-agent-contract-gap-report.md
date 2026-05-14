# Commerce Sales Agent Contract Gap Report

Status: M12 gap analysis and safe regression coverage only. This pass did not deploy, merge, create cloud resources, change production config, enable live payments, enable live Plural, touch production secrets, or commit local-only artifacts.

## Classification Key

- `done`: implemented and covered enough for the internal mock-provider sandbox contract.
- `partial`: a meaningful slice exists, but the V1 contract is not complete.
- `blocked`: implementation depends on missing hosted staging, external contract, or safety design.
- `deferred`: intentionally later than the current V1 Sales Agent slice.
- `not-started`: no concrete AgenticOrg runtime/eval path exists yet.

## Brutal Summary

| Area | Status | Assessment |
| --- | --- | --- |
| Grantex-only commerce connector | `done` | `GrantexCommerceConnector` exposes `grantex_commerce:*` aliases and does not import Stripe, Plural, Pine, or provider credential connectors. |
| Safe tool aliases | `done` | Aliases map to Grantex MCP tools plus Grantex passport REST endpoints: catalog, inventory, cart, consent, passport exchange, payment intent, checkout, and status. |
| Consent/passport guardrails | `partial` | Local guardrails refuse missing, denied, revoked, or expired passport inputs; final cryptographic verification remains Grantex-owned and hosted staging is not yet exercised. |
| Amount cap guardrails | `done` | Local guardrails refuse requested amounts above passport cap before calling Grantex. |
| Disabled merchant/agent guardrails | `partial` | Local status/error handling exists; real staging disabled merchant and untrusted agent cases are not yet executed. |
| Stale inventory behavior | `done` | Local guardrails require cautious responses for stale or unknown inventory. |
| Unsupported EMI/discount/warranty behavior | `done` | Claim grounding refuses unbacked EMI, discount, offer, return, tax, and warranty claims. |
| No direct Stripe/Plural/Pine/provider credential path | `done` | Regression tests statically block provider imports and default Commerce Sales Agent tools are Grantex-only. |
| Mocked eval/demo status | `partial` | Local demo and evals cover the safety path, but they use mocked Grantex responses. |
| Real hosted staging eval gap | `blocked` | M11 documents commands only; non-mocked hosted staging connector/demo/eval mode and hosted services do not exist yet. |
| Broader PRD Commerce Agent Pack | `deferred` | Only the Sales Agent slice exists; catalog enrichment, offer, support, reconciliation, and store operations agent pack work remains roadmap. |

## Contract Surface Inventory

| Contract item | Status | Current evidence | Gap |
| --- | --- | --- | --- |
| Grantex-only connector | `done` | `connectors/commerce/grantex_commerce.py` uses `/mcp` and Grantex passport REST endpoints. | Hosted staging evidence pending. |
| `merchant_get_profile` alias | `done` | Maps to `merchant.get_profile`. | None for local contract. |
| `catalog_search` alias | `done` | Maps to `catalog.search`. | Depends on Grantex product data completeness. |
| `catalog_get_item` alias | `done` | Maps to `catalog.get_item`. | Depends on Grantex product data completeness. |
| `inventory_check` alias | `done` | Maps to `inventory.check`. | Hosted stale inventory negative case pending. |
| `cart_create` alias | `done` | Requires idempotency key and maps to `cart.create`. | Hosted staging evidence pending. |
| `consent_request` alias | `done` | Calls `/v1/commerce/passports/consent-requests`. | Hosted staging consent UX and delivery pending. |
| `consent_exchange` alias | `done` | Calls `/v1/commerce/passports/exchange`. | Hosted staging approval flow pending. |
| `payment_create_intent` alias | `done` | Requires idempotency key, local guardrail pass, then maps to `payment.create_intent`. | Hosted staging evidence pending. |
| `checkout_create` alias | `done` | Requires idempotency key, local guardrail pass, then maps to `checkout.create`. | Hosted staging evidence pending. |
| `payment_get_status` alias | `done` | Maps to `payment.get_status`. | Hosted staging evidence pending. |
| Production default base URL | `partial` | Connector default is production-shaped when env is absent. | Staging must set `GRANTEX_COMMERCE_BASE_URL=https://api-staging.grantex.dev`; future hosted mode should fail closed when staging env is absent. |
| Mocked demo | `partial` | `demos/commerce_sales_agent_demo.py` proves ordered Grantex aliases and no provider credential handling. | It is not hosted evidence and must not be reported as such. |
| Mocked evals | `partial` | `tests/evals/test_commerce_sales_agent_evals.py` covers 14 local mocked cases. | A real-staging eval mode is still blocked. |
| Direct provider calls | `done` | `tests/regression/test_commerce_sales_agent_no_provider_calls.py` blocks provider imports/calls in commerce code. | Keep this static guard whenever adding agent pack features. |

## Guardrail Coverage

| Guardrail | Status | Evidence | Remaining gap |
| --- | --- | --- | --- |
| Missing consent | `done` | Local guardrail refuses payment/checkout without passport material. | Hosted staging negative case pending. |
| Denied consent | `done` | Local guardrail refuses denied/rejected/revoked/withdrawn/failed consent statuses. | Hosted staging negative case pending. |
| Revoked passport | `done` | Local guardrail refuses `passport_status=revoked`. | Hosted staging negative case pending. |
| Expired passport | `done` | Local guardrail refuses `passport_status=expired`. | Hosted staging negative case pending. |
| Amount cap breach | `done` | Local guardrail compares requested amount to passport cap. | Hosted staging negative case pending. |
| Disabled merchant | `partial` | Local explicit status/error refusal exists. | Hosted staging disabled merchant case pending. |
| Untrusted agent | `partial` | Local explicit status refusal exists. | Hosted staging untrusted agent case pending. |
| Stale inventory | `done` | `inventory_caution` refuses to guarantee stale or unknown stock. | Hosted staging negative case pending. |
| Unsupported EMI | `done` | Claim grounding refuses unbacked EMI claims. | Hosted staging negative case pending. |
| Unsupported discount | `done` | Claim grounding refuses unbacked discount claims. | Hosted staging negative case pending. |
| Unsupported warranty | `done` | Claim grounding refuses unbacked warranty claims. | Hosted staging negative case pending. |

## No-Provider-Call Boundary

No direct Stripe/Plural/Pine/provider credential commerce path is allowed.

AgenticOrg Commerce Sales Agent must not:

- call Stripe, Plural, Pine, or payment provider connectors for commerce;
- read provider credentials for commerce;
- create checkout links outside Grantex;
- poll provider status directly;
- retry a refused Grantex payment through another provider path.

The only commerce execution path is Grantex:

- `GRANTEX_COMMERCE_BASE_URL=https://api-staging.grantex.dev` for hosted staging;
- `GRANTEX_BASE_URL=https://api-staging.grantex.dev` for hosted staging fallback;
- one staging-only connector auth material source by name: `GRANTEX_COMMERCE_BEARER_TOKEN`, `GRANTEX_AGENT_ASSERTION`, or `GRANTEX_API_KEY`.

Do not record values for those names.

## Real Hosted Staging Gap

The real-staging gap is still blocked because:

- Grantex hosted staging infrastructure does not exist yet.
- AgenticOrg hosted staging services do not exist yet.
- `demos/commerce_sales_agent_demo.py --mode=hosted-staging` is only a documented command plan.
- `python -m pytest tests/evals/test_commerce_sales_agent_evals.py -q --hosted-staging` is only a documented command plan.
- The current local demo/eval path uses mocked Grantex responses.
- No redacted hosted staging evidence exists yet.

## Broader PRD Commerce Agent Pack Gap

The PRD calls for AgenticOrg commerce agents beyond the current Sales Agent. Current status:

| Agent pack item | Status | Reason |
| --- | --- | --- |
| Commerce Sales Agent | `partial` | Local mock/eval path exists; hosted staging proof is blocked. |
| Catalog enrichment agent | `deferred` | Requires Grantex catalog list/bulk/CSV completion first. |
| Offer agent | `deferred` | Requires Grantex offers/discount/EMI data contract. |
| Support agent | `deferred` | Requires order/support/refund capability contract. |
| Reconciliation agent | `deferred` | Requires provider reconciliation and audit export contract. |
| Store operations agent | `deferred` | Requires inventory/location/POS contract. |

## Safe Fixes Made In M12

- Added this gap report.
- Added regression coverage that pins no-provider-call language, real-staging gap language, staging URL usage, status classifications, and no secret values.
- Did not implement real hosted staging mode, provider integration, broad agent pack features, or production config changes.

## Exact Future Implementation Prompts

### AgenticOrg Real Hosted Staging Eval Mode

`Task: M12A-Agent only - implement AgenticOrg real hosted staging eval mode for the Commerce Sales Agent after Grantex staging exists. Do not deploy, merge, create cloud resources, change production config, enable live payments, or enable live Plural. Add an explicit hosted-staging mode that refuses production URLs, requires GRANTEX_COMMERCE_BASE_URL=https://api-staging.grantex.dev, uses only grantex_commerce:* tools, redacts auth/passport/idempotency material, and produces a redacted evidence report. Add focused tests for production URL refusal, missing staging env refusal, no direct provider calls, and no mocked results being reported as hosted evidence.`

### Broader Commerce Agent Pack Planning

`Task: Commerce Agent Pack planning only - map the broader AgenticOrg Commerce Agent Pack against Grantex-owned source-of-truth APIs. Do not implement provider calls. Define catalog enrichment, offer, support, reconciliation, and store operations agent requirements, and mark each as blocked until corresponding Grantex catalog/offers/order/reconciliation/POS contracts exist. Add regression tests that keep all commerce payment-affecting work on Grantex tools only.`
