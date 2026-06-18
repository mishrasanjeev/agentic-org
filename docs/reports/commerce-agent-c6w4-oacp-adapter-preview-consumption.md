# Commerce Agent C6W4 - OACP Adapter Preview Consumption

Status: implementation foundation, internal-only, non-enabling.

## Scope

C6W4 adds local AgenticOrg handling rules for Grantex OACP adapter previews.

This slice adds:

- Local preview-use checks for schema.org JSON-LD style discovery, UCP-style capability profiles, ACP-style commerce capability shapes, AP2-style evidence and intent summaries, A2A-style agent card/task capabilities, and MCP-style tool/resource capabilities.
- Channel-safe summary output that preserves source artifact IDs, artifact families, freshness tier, and unsupported capabilities.
- Focused tests proving adapter previews remain non-binding and buyer-safe.

AgenticOrg does not invent commerce facts. It consumes Grantex-sourced preview metadata and must keep source/freshness wording visible to buyer and seller surfaces.

adapter previews are not transaction authority. They can route or display sourced facts, but cannot approve commerce actions.

No endpoint, migration, workflow, provider adapter, public discovery, checkout/payment, live provider, merchant private API, allowlist, cloud, deploy, or external protocol publication behavior is added.

## Buyer-Agent Handling

Buyer agents may use adapter previews for:

- Browse.
- Compare.
- Draft-cart explanation.
- Quote-preview explanation.
- Policy explanation.
- Seller-card display.
- Agent routing.
- Tool discovery.

Buyer agents must preserve:

- Source artifact IDs.
- Source artifact families.
- Source authority.
- Freshness tier.
- Unsupported capability wording.
- Explicit non-authoritative transaction wording.

## Seller-Agent Handling

Seller agents may use adapter previews to explain:

- Which Grantex artifacts supplied the preview facts.
- Which surfaces can display non-binding facts.
- Which capabilities are unsupported or blocked.
- Which remediation still requires merchant systems or provider rails.

Seller agents must not treat adapter previews as merchant approval, payment approval, checkout approval, public discovery approval, or provider execution authority.

## Third-Party Agent Cards

Third-party agent cards are bounded by the preview safety contract:

- The preview surface must be one of the C6W4 surfaces.
- The preview must cite Grantex source artifacts.
- non_authoritative_for_transaction must be true.
- no_checkout_payment_enablement, no_live_provider_enablement, and no_public_discovery_enablement must be true.
- Missing flags or missing source references fail closed.

## Blocked Actions

Adapter previews refuse:

- Checkout creation.
- Payment authorization or capture.
- Refund execution.
- Settlement, payout, fulfillment start, or merchant approval.
- Public discovery publish or unpublish.
- Live provider or live Plural use.
- Provider, carrier, shipping provider, or merchant private API calls.
- Protocol publication or external submission.

## What This Does Not Enable

C6W4 does not enable:

- Public discovery.
- Production Commerce V1.
- Checkout/payment creation.
- Payment capture or debit.
- Live payments.
- Live provider use.
- Live Plural use.
- Provider calls.
- Carrier or shipping provider calls.
- Merchant private API calls.
- Connector credential export.
- Production allowlists.
- Public OACP publication.
- External protocol submission.
- Certification, compliance, conformance, standardization, production readiness, public-launch readiness, merchant approval, checkout approval, payment approval, live provider readiness, or OACP public readiness claims.

## Stop Conditions

Stop later implementation if:

- Adapter previews are used as final commerce authority.
- Buyer-facing text drops source/freshness/unsupported capability wording.
- AgenticOrg creates checkout/payment, live provider, live Plural, public discovery, or external protocol publication behavior from a preview.
- AgenticOrg calls provider, carrier, shipping provider, or merchant private APIs from a preview.
- AgenticOrg receives raw credentials, raw connector payloads, raw provider payloads, private merchant API values, or production allowlists.
