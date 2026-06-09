# C6U4 Source/Freshness and Buyer-Safe Commercial Fact Projection

Status: internal implementation report only. This does not approve production launch, real merchants, public discovery, checkout/payment, live providers, live Plural, production config, production allowlists, cloud resources, provider calls, merchant private API calls, protocol publication, external submission, certification, compliance, conformance, standardization, public-launch readiness, merchant approval, or production readiness.

## Summary

C6U4 adds a buyer-safe commercial fact projection for AgenticOrg read-only commerce previews. AgenticOrg still treats Grantex as the only source of truth. The projection qualifies preview price, inventory, source, and freshness facts and refuses or marks unsupported facts when Grantex does not provide buyer-safe evidence.

No public discovery, checkout/payment, live provider, live Plural, provider call, merchant private API call, production config, production allowlist, workflow, migration, dependency, cloud resource, or protocol publication behavior is added.

## Projection Model

| Fact | Buyer-safe projection |
| --- | --- |
| Preview price | Shown as preview-only when Grantex provides amount and currency. `final_price_confirmed` remains false. |
| Tax/GST | Marked unknown unless Grantex provides explicit tax metadata. Final checkout tax is not confirmed here. |
| Warranty | Summary may be shown only when Grantex provides a public-safe summary. Missing summary stays unknown. |
| Return/refund | Return summary may be shown only when Grantex provides a public-safe summary. Refund execution remains unsupported. |
| Inventory | Availability is a preview bucket only. Unknown or stale data becomes caution, not a stock promise. |
| Source/freshness | Shows a safe source label, freshness status, and optional timestamp. Private labels become `grantex_controlled_source`. |
| Delivery/support/fulfillment | Unsupported until Grantex provides explicit buyer-safe contracts. |
| Settlement/payout/reconciliation | Unsupported in buyer discovery. |
| Discounts/coupons/EMI | Unsupported unless Grantex provides explicit buyer-safe facts in a later slice. |

## Runtime Scope

Changed runtime behavior is defensive only:

- `buyer_discovery.py` now projects `commercial_facts` and `source_summary` for capped Grantex catalog samples.
- `buyer_session.py` preserves nested safe projection fields in channel-neutral responses.
- `sales_guardrails.py` expands unsupported claim detection for final price, inventory, delivery, fulfillment, refund, settlement, payout, support, discount, coupon, and EMI language.

These helpers do not call a provider, Plural, a merchant private API, or any new Grantex endpoint. They do not enable writes or public discovery.

## Refusal Rules

AgenticOrg must refuse or qualify:

- Final price, checkout total, or tax-inclusive claims without Grantex final-price facts.
- Tax/GST claims without Grantex tax facts.
- Warranty claims without Grantex warranty facts.
- Return/refund claims without Grantex return/refund facts.
- Delivery, fulfillment, tracking, support, settlement, payout, discount, coupon, and EMI claims without Grantex source facts.
- Any stale or unknown inventory as an availability guarantee.
- Any private source label, private URL, raw connector payload, credential, token, passport/JWT value, DB/Redis URL, webhook secret, or merchant-private identifier.

## Safe Buyer Wording

- "This is a Grantex preview price, not a final checkout total."
- "Final tax, fees, discounts, delivery charges, and checkout totals are not confirmed."
- "Inventory is stale or unknown, so I cannot confirm availability."
- "Warranty and return terms are unknown unless Grantex provides summaries."
- "Delivery, refunds, settlement, payout, and support execution are not enabled in this slice."

## Unsafe Buyer Wording

The guardrails must refuse or rewrite:

- "This is the final price with all taxes included."
- "Guaranteed in stock."
- "Guaranteed delivery tomorrow."
- "Guaranteed refund."
- "I checked the merchant private system."
- "The provider approved this payment."

## Tests

The C6U4 regression test covers:

- Preview price projected as non-final when final tax is missing.
- Missing warranty, return, delivery, support, fulfillment, refund, settlement, and payout facts are not invented.
- Stale inventory becomes caution and never a stock promise.
- Private source metadata, raw payload markers, credentials, internal tenant/merchant IDs, and private URLs are omitted or redacted.
- Channel-neutral buyer responses preserve the safe projection while public discovery stays hidden.
- Unsupported commercial claims are refused when Grantex source facts are missing.

Nearby buyer discovery/session tests were updated to assert the new nested safe shape without relaxing redaction.

## Remaining Gaps

- Shared public discovery state contract.
- Consent/session/passport revocation propagation.
- Channel-specific refusal packs.
- Order, fulfillment, refund, support, settlement, and payout contracts.
- Sandbox checkout E2E.
- Live provider readiness.
- AgenticOrg CI/CD cloud-build guard follow-up.

## Stop Conditions

Stop any C6U4 follow-up if it adds public discovery enablement, checkout/payment enablement, live payment, live Plural, provider calls, merchant private API calls, production config, production allowlists, cloud resources, secrets, workflow changes, migrations, protocol publication, external submission, certification, compliance, conformance, standardization, merchant approval, or production readiness claims.
