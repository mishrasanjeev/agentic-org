# AgenticOrg OACP Runtime Docs

> OACP work in AgenticOrg is owned as part of AgenticOrg by
> **Orchestrum Technologies LLP**. Inventor / Owner: **Sanjeev Kumar**. Contact
> [sanjeev@orchestrum.in](mailto:sanjeev@orchestrum.in) or
> [mishra.sanjeev@gmail.com](mailto:mishra.sanjeev@gmail.com). See
> [AgenticOrg ownership and contact](../OWNERSHIP.md).

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md). Launch closure source of truth: [OACP Runtime Launch Closure PRD](runtime-launch-closure-prd.md).

Current cross-product capability status: [AgenticOrg product status](../PRODUCT_STATUS.md).

AgenticOrg owns the buyer and seller AI-agent runtime for OACP-backed commerce, including purchase preparation and Offline POS handoff orchestration. Grantex owns OACP trust authority, protocol/policy governance, canonical artifacts, artifact verification, and protocol adapter authority. Shopify, POS, and merchant systems remain source of record. Pine Labs Plural/P3P, POS, and payment providers own mandate/payment/POS execution.

Merchants configure the runtime in `/dashboard/commerce-runtime` during onboarding or later. The configuration is scoped by tenant, merchant, and seller agent, and covers source connectors, buyer channels, provider-owned payment rails, public publishing, and Offline POS metadata. Shopify is the shipped read-only runtime source connector today and has been exercised against a configured production tenant. WooCommerce, ERP, PIM, OMS, WMS, custom API, bank-owned rails, fintech rails, and custom payment providers can be saved as non-executing adapter-ready config until approved adapters exist.

## Current Runtime Status

| Status | Capability |
| --- | --- |
| Shipped | Merchant configuration, Seller Commerce Agent onboarding, encrypted Shopify credential custody, read-only Shopify Admin GraphQL sync, signed webhook verification, Grantex authority request, durable OACP cache, buyer-safe answers, protocol payload generation, bridge routes, purchase preparation, and Offline POS handoff/reconciliation |
| Configuration-dependent | Merchant public catalog, WhatsApp, Telegram, Grantex tenant authority, Pine/Plural capability verification, and real POS/provider callbacks |
| Not universal | WooCommerce/ERP/PIM/OMS/WMS runtime adapters, external channel marketplace approval, payment/order execution, and public OACP certification or standardization |

Protocol adapter payloads make the same public-safe commerce facts available in
bounded Schema.org, UCP-style, ACP-style, AP2-style, A2A, MCP, and OpenAPI
shapes. They do not prove external program approval or create payment/order
authority.

## Runtime Docs

- [Truth inventory](truth-inventory.md)
- [Runtime launch closure PRD](runtime-launch-closure-prd.md)
- [OACP end-user flow](end-user-flow.md)
- [Merchant commerce configuration](merchant-commerce-configuration.md)
- [Seller Commerce Agent onboarding](seller-commerce-agent-onboarding.md)
- [Shopify merchant onboarding](shopify-merchant-onboarding.md)
- [Shopify connector setup](shopify-connector-setup.md)
- [Buyer agent flow](buyer-agent-flow.md)
- [Buyer-surface bridge guide](buyer-surface-bridge-guide.md)
- [Artifact cache guide](artifact-cache-guide.md)
- [Protocol adapter consumption guide](protocol-adapter-consumption-guide.md)
- [Plural/Pine P3P capability verifier](plural-pine-p3p-capability-verifier.md)
- [Purchase/mandate handoff](purchase-mandate-handoff.md)
- [Offline POS bridge](offline-pos-bridge.md)
- [Runtime operations runbook](runtime-operations-runbook.md)
- [Troubleshooting](troubleshooting.md)

## Explainers

- [Move your Shopify store to Agentic Commerce](explainers/shopify-to-agentic-commerce.md)
- [How buyer agents shop safely with OACP](explainers/buyer-agent-safety.md)
- [Build against OACP artifacts and bridges](explainers/developer-artifacts-bridges.md)
- [How OACP maps to Schema.org, UCP, ACP, AP2, A2A, MCP](explainers/protocol-partner-mappings.md)
- [Provider-owned mandate/payment evidence in OACP](explainers/provider-owned-payment-evidence.md)
- [Launch and rollback runbook](explainers/launch-rollback-runbook.md)
