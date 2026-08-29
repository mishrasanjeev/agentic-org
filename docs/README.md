# AgenticOrg Documentation

This directory contains current product guides, operator runbooks, design
documents, historical reviews, and retained release evidence. Start here rather
than treating every file in `docs/` as a description of the current product.

## Current Sources Of Truth

Use these documents in this order:

1. [Current product status](PRODUCT_STATUS.md) for what is shipped,
   configuration-dependent, or not shipped.
2. Runtime OpenAPI and [route inventory](route_inventory.json) for current HTTP
   paths and authentication metadata.
3. [Architecture](architecture.md) for the current platform shape and explicit
   historical sections.
4. [Deployment](deployment.md) and [production smoke](runbooks/production_smoke.md)
   for release operations.
5. Feature runbooks for setup and operating boundaries.
6. Historical reports only when investigating why a decision was made.

Live version and registry totals come from
[`GET /api/v1/product-facts`](https://app.agenticorg.ai/api/v1/product-facts).
Service health comes from
[`GET /api/v1/health`](https://app.agenticorg.ai/api/v1/health).

## Product Guides

| Need | Start here |
| --- | --- |
| Understand the platform | [Current product status](PRODUCT_STATUS.md), [why AgenticOrg](why-agenticorg.md) |
| Build and govern agents | [Agents](agents.md), [agent workflows](agent-workflows.md), [testing](TEST_PLAN.md) |
| Upload documents and scans | [Knowledge ingestion and OCR](knowledge-ingestion.md) |
| Configure voice agents | [Voice runtime](voice-runtime.md) |
| Run browser automation | [RPA runtime](rpa-runtime.md) |
| Integrate through APIs | [API reference](api-reference.md), generated OpenAPI, [Python SDK](../sdk/README.md), [TypeScript SDK](../sdk-ts/README.md) |
| Connect an MCP client | [MCP server](../mcp-server/README.md) |
| Implement agentic commerce | [OACP documentation](oacp/README.md) |
| Operate production | [Deployment](deployment.md), [runbooks](RUNBOOKS.md), [backup and recovery](BACKUP_AND_DR.md) |
| Review security | [Security policy](../SECURITY.md), [vulnerability disclosure](VULNERABILITY_DISCLOSURE.md) |

## OACP And Agentic Commerce

The canonical OACP flow is [OACP end-user flow](oacp/end-user-flow.md).
AgenticOrg owns buyer and seller agent runtime. Grantex owns trust, policy, and
canonical artifact authority. Merchant systems own catalog, inventory, order,
and support truth. Payment providers, banks, and POS systems own execution.

Current runtime guides:

- [Merchant commerce configuration](oacp/merchant-commerce-configuration.md)
- [Seller Commerce Agent onboarding](oacp/seller-commerce-agent-onboarding.md)
- [Shopify merchant onboarding](oacp/shopify-merchant-onboarding.md)
- [Artifact cache](oacp/artifact-cache-guide.md)
- [Buyer surfaces](oacp/buyer-surface-bridge-guide.md)
- [Protocol adapter payloads](oacp/protocol-adapter-consumption-guide.md)
- [Provider capability evidence](oacp/plural-pine-p3p-capability-verifier.md)
- [Purchase handoff](oacp/purchase-mandate-handoff.md)
- [Offline POS bridge](oacp/offline-pos-bridge.md)
- [Operations and rollback](oacp/runtime-operations-runbook.md)

## Planning And Readiness

These documents track product gaps and promotion evidence. They are planning
and review sources, not proof that every registered capability is production
ready.

- [Readiness program](readiness/README.md)
- [Gap analysis](readiness/GAP_ANALYSIS.md)
- [Domain readiness standard](readiness/DOMAIN_READINESS_STANDARD.md)
- [Capability readiness register](readiness/CAPABILITY_READINESS_REGISTER.md)
- [Build roadmap](readiness/BUILD_ROADMAP.md)
- [Landing and documentation blueprint](readiness/LANDING_AND_DOCUMENTATION_BLUEPRINT.md)
- [Program memory](readiness/PROGRAM_MEMORY.md)

## Documentation Status Labels

- **Current**: maintained against the present runtime or operating process.
- **Configuration-dependent**: implemented, but requires tenant credentials,
  scopes, provider access, or an explicit feature setting.
- **Historical**: a point-in-time review, PRD, test report, or implementation
  slice. It may explain history but must not override current runtime evidence.
- **Target design**: intended behavior that is not proof of implementation.

Files in `docs/reports/`, dated audits, old gap reviews, and C6/C6W/C6X/C6Y
slice documents are historical unless a current guide links to them as retained
evidence. Their presence does not prove production availability.

## Documentation Change Rule

When behavior changes, update the matching guide, root `README.md`, public page
copy, SDK documentation, generated `llms.txt` assets, and tests in the same pull
request. Do not copy registry totals into prose. Do not describe a connector,
provider rail, marketplace listing, or external channel as live solely because
its code or configuration schema exists.
