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
5. Configure or verify the merchant Shopify connector through AgenticOrg:
   preferred route `POST /commerce/runtime/seller-agents/connectors/shopify/credentials`;
   storage target tenant-aware encrypted `ConnectorConfig`; response values redacted.
6. Run Shopify Admin GraphQL read-only sync. It must prefer the merchant connector config and only fall back to legacy environment credentials when no merchant config exists.
7. Send the AgenticOrg authority request to Grantex.
8. Cache Grantex artifacts in AgenticOrg.
9. Ask a buyer product question from cached artifacts and record source/freshness labels.
10. Smoke web, MCP, OpenAPI/function, and A2A bridge contracts against the cached products.
11. Confirm WhatsApp and Telegram adapters either answer through the same bridge contract or return `blocked_missing_credentials` with no outbound send.
12. Run Plural/Pine capability metadata verification only.

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

Follow-up implementation in this PR adds the encrypted merchant Shopify connector setup route, the expanded onboarding lifecycle, 11-family Grantex artifact cache acceptance, web/OpenAPI/A2A/WhatsApp/Telegram bridge endpoints, and a local launch evidence harness. After merge/deploy, the Shopify blocker should be handled through the AgenticOrg merchant connector path instead of a single global Shopify token. Grantex tenant-token provisioning remains separate.
