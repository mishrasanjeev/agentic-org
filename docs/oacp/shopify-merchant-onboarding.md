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

1. Merchant opens `/dashboard/commerce-runtime`.
2. Merchant saves tenant/merchant/seller scoped commerce config with source connector `Shopify`, buyer channels, payment provider, public publishing, and Offline POS preferences.
3. Merchant authorizes read-only Shopify access.
4. AgenticOrg validates credentials and updates the merchant config source ref.
5. AgenticOrg syncs Shopify config into a Seller Commerce Agent onboarding packet.
6. AgenticOrg creates a redacted source evidence packet.
7. Grantex issues or refuses OACP artifacts.
8. Buyer surfaces are enabled only after source labels and blockers are verified.

The same config UI accepts WooCommerce, ERP, PIM, OMS, WMS, and custom API metadata for future adapters, but only Shopify has a runtime sync path today.

## User Promise

Buyer agents can answer from source-labeled snapshots. They cannot claim that a checkout, payment, order, or mandate succeeded unless the merchant/provider system confirms it through an approved path.
