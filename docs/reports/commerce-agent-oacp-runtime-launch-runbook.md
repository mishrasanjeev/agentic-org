# Commerce Agent OACP Runtime Launch Runbook

Status: internal C6Z production runbook.

Last updated: 2026-06-18.

## Canonical Architecture

- AgenticOrg is the buyer and seller AI-agent runtime.
- Grantex is the trust, protocol, policy, and canonical OACP artifact authority.
- Shopify and other merchant systems remain operational systems of record.
- Plural/Pine and other provider or fintech rails own mandate and payment execution.
- Grantex is not a toll booth for every non-binding buyer/seller interaction.

## Safe Run Steps

1. Confirm AgenticOrg health and deployed SHA.
2. Confirm Grantex health and deployed SHA.
3. Confirm public discovery remains disabled.
4. Create or reuse a Seller Commerce Agent onboarding packet for the approved Shopify pilot merchant.
5. Run Shopify Admin GraphQL read-only sync.
6. Send the AgenticOrg authority request to Grantex.
7. Cache Grantex artifacts in AgenticOrg.
8. Ask a buyer product question from cached artifacts and record source/freshness labels.
9. Smoke MCP seller facts against the cached products.
10. Run Plural/Pine capability metadata verification only.

## Hard Stops

Stop immediately if any check attempts or requires:

- checkout, payment, order, mandate, refund, return, shipment, inventory hold, live-provider execution, merchant-private API mutation, production allowlist changes, public discovery publication, OACP external publication, or certification/conformance/standardization claims;
- printing secrets, tokens, raw Shopify payloads, raw provider payloads, raw Grantex artifacts, JWTs, passports, DB URLs, Redis URLs, or Secret Manager values.

## Current Production Result

The 2026-06-18 production run is blocked:

- Shopify Admin GraphQL read-only sync returns `401 Unauthorized` using the mounted AgenticOrg C6Z Shopify token.
- Direct status-only Shopify GraphQL probe with the same token returns `401`.
- Grantex C6Z authority called with AgenticOrg's configured internal token returns `422 tenant_not_provisioned`.

Do not report internal runtime demo, closed merchant pilot, or public OACP preview as complete until both blockers are fixed and the full vertical is re-run.
