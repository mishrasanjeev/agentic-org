# AgenticOrg OACP Runtime Docs

> OACP work in AgenticOrg is owned as part of AgenticOrg by
> **Orchestrum Technologies LLP**. Inventor / Owner: **Sanjeev Kumar**. Contact
> [sanjeev@orchestrum.in](mailto:sanjeev@orchestrum.in) or
> [mishra.sanjeev@gmail.com](mailto:mishra.sanjeev@gmail.com). See
> [AgenticOrg ownership and contact](../OWNERSHIP.md).

Canonical end-to-end flow: [OACP end-user flow](end-user-flow.md). Launch closure source of truth: [OACP Runtime Launch Closure PRD](runtime-launch-closure-prd.md).

For merchant-approved external buyer agents and the synchronous A2A v1
HTTP+JSON interface, see [A2A interoperability](../a2a-interoperability.md).
For a runnable synthetic local seller and standalone external buyer, see the
[A2A commerce demo](../a2a-commerce-demo.md). It does not execute purchases.

Current cross-product capability status: [AgenticOrg product status](../PRODUCT_STATUS.md).

AgenticOrg owns the buyer and seller AI-agent runtime for OACP-backed commerce, including purchase preparation, Offline POS handoff orchestration, the commerce arbiter and ledger, and the flag-gated UCP and ACP surfaces. Grantex owns OACP trust authority, protocol/policy governance, canonical artifacts and their signing keys, and protocol adapter authority. AgenticOrg verifies every Grantex artifact (ES256 detached JWS, scope, TTL, Token Status List) before caching it; see the [artifact cache guide](artifact-cache-guide.md). Shopify, POS, and merchant systems remain source of record. Pine Labs Plural/P3P, POS, and payment providers own mandate/payment/POS execution.

**Status on 2026-10-20.** The `main` branch carries artifact verification before caching, the
commerce arbiter and ledger, the UCP business profile, catalog, checkout, cart and order routes,
the ACP feed and checkout sessions, the MCP UCP checkout tools, server-verified Grantex
mandates, and the Plural executor with refunds, UPI AutoPay and RBI AFA. Everything that touches
checkout, payment, mandates or public discovery is behind a flag that defaults to off; with the
defaults no executor is registered, so a checkout Complete ends at `executor_unavailable`. The
AgenticOrg production deployment does not include this work yet; check the deployed commit before
relying on any of it there. Grantex production issues ES256-signed OACP artifacts under its first
OACP issuer key and serves its public Token Status List. The turn-on order is in the
[launch and rollback runbook](explainers/launch-rollback-runbook.md).

Merchants configure the runtime in `/dashboard/commerce-runtime` during onboarding or later. The configuration is scoped by tenant, merchant, and seller agent, and covers source connectors, buyer channels, provider-owned payment rails, public publishing, and Offline POS metadata. Shopify is the shipped read-only runtime source connector today and has been exercised against a configured production tenant. WooCommerce, ERP, PIM, OMS, WMS, custom API, bank-owned rails, fintech rails, and custom payment providers can be saved as non-executing adapter-ready config until approved adapters exist.

## Current Runtime Status

| Status | Capability |
| --- | --- |
| Shipped (in source on `main`) | Merchant configuration, Seller Commerce Agent onboarding, encrypted Shopify credential custody, read-only Shopify Admin GraphQL sync, signed webhook verification, Grantex authority request, durable OACP cache that verifies each artifact before storing it, buyer-safe answers, protocol payload generation, bridge routes, purchase preparation, Offline POS handoff/reconciliation, and the commerce arbiter and ledger (every commerce write recorded; purchase and POS handoffs decided `allow` or `refuse`) |
| Behind a flag, default off | UCP business profile, catalog and locations (`AGENTICORG_UCP_PROFILE_ENABLED`); UCP checkout, cart and order (`AGENTICORG_UCP_CHECKOUT_ENABLED`); ACP product feed (`AGENTICORG_ACP_FEED_ENABLED`); ACP checkout sessions (`AGENTICORG_ACP_CHECKOUT_ENABLED`); Grantex mandates (`AGENTICORG_GRANTEX_MANDATES_ENABLED`); Plural payment execution and refunds (`PLURAL_PINE_LIVE_EXECUTION_ENABLED`); audit-chain sealing for commerce tenants, witness checkpoints and retention |
| Configuration-dependent | Merchant public catalog, WhatsApp, Telegram, Grantex tenant authority and service token, Pine/Plural capability verification, real POS/provider callbacks, and a DNS-attested serving host for UCP and ACP |
| Not universal | WooCommerce/ERP/PIM/OMS/WMS runtime adapters, external channel marketplace approval, payment/order execution for rails other than Plural, and public OACP certification or standardization |

Protocol adapter payloads make the same public-safe commerce facts available in
bounded shapes: Schema.org, the merchant's UCP business profile (spec shape,
only for a published merchant), ACP-style and AP2-style compatibility mappings,
A2A, MCP, and OpenAPI. They do not prove external program approval or create
payment/order authority. The served UCP and ACP surfaces are separate routes,
listed above.

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
- [Commerce arbiter and ledger](arbiter-ledger.md)
- [UCP business profile, catalog and locations](ucp-profile.md)
- [UCP checkout, cart and order](ucp-checkout.md)
- [Merchant custom domains](merchant-custom-domains.md) and the [apex domain guide](merchant-apex-domain.md)
- [ACP product feed and checkout sessions](acp-feed-and-sessions.md)
- [Agent request signing (RFC 9421, Visa TAP, Web Bot Auth)](agent-request-signing.md)
- [Plural/Pine P3P capability verifier](plural-pine-p3p-capability-verifier.md)
- [Plural executor](plural-executor.md)
- [Mandates, UPI AutoPay and RBI AFA](mandates.md)
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
