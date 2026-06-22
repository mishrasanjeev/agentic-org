# Shopify Merchant Onboarding Guide

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md).

Shopify remains the source of record. AgenticOrg syncs public-safe product, variant, price, image, status, and inventory evidence without moving merchant truth into Grantex.

## Requirements

| Requirement | Notes |
| --- | --- |
| Public `*.myshopify.com` domain | The runtime validates host shape. |
| Read-only Admin API token or OAuth app flow | Stored by AgenticOrg credential custody, not in OACP artifacts. |
| Required scopes | Product and inventory read scopes only for the current OACP path. |
| Grantex tenant allowlist | Required before authority artifacts issue. |
| Provider setup | Required only for mandate/payment capability evidence. |

## Onboarding Timeline

1. Merchant authorizes read-only Shopify access.
2. AgenticOrg validates credentials.
3. AgenticOrg creates a redacted source evidence packet.
4. Grantex issues or refuses OACP artifacts.
5. Buyer surfaces are enabled only after source labels and blockers are verified.

## User Promise

Buyer agents can answer from source-labeled snapshots. They cannot claim that a checkout, payment, order, or mandate succeeded unless the merchant/provider system confirms it through an approved path.
