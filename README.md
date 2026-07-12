# AgenticOrg

AgenticOrg is an Apache-2.0 enterprise AI agent platform for building, running, and governing agent workflows across finance, HR, marketing, operations, back office, communications, and bounded agentic-commerce use cases.

[Website](https://agenticorg.ai) | [Application](https://app.agenticorg.ai) | [Playground](https://agenticorg.ai/playground) | [Documentation](docs/) | [Security policy](SECURITY.md)

![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB)
![React 19](https://img.shields.io/badge/React-19-149ECA)
![License: Apache 2.0](https://img.shields.io/badge/License-Apache--2.0-blue)

## Source of truth

Live product totals and the deployed product version are published by the public [product facts endpoint](https://app.agenticorg.ai/api/v1/product-facts):

    GET https://app.agenticorg.ai/api/v1/product-facts

The response contains version, agent_count, connector_count, and tool_count. Do not copy those values into documentation, page copy, or tests: they are computed from runtime registries and can change independently of a documentation release. The version declared for source builds is in [pyproject.toml](pyproject.toml).

Billing limits and list prices come from [core/billing/limits.py](core/billing/limits.py). Production deployment behavior comes from [scripts/deploy_cloud_run.sh](scripts/deploy_cloud_run.sh).

## What is in this repository

| Area | Current implementation |
| --- | --- |
| Agent runtime | FastAPI and LangGraph runtime with model routing, tool invocation, confidence handling, retry policy, and persisted execution state |
| Agent authoring | Built-in agent definitions, custom agents, prompt templates, organization hierarchy, and a no-code UI |
| Workflows | Workflow definitions, runs, schedules, conditions, approvals, and audit events |
| Governance | Tenant and role boundaries, scoped tools, human-in-the-loop queues, shadow evaluation, policy checks, and kill-switch patterns |
| Integrations | Native connector registry plus optional integration gateways; actual availability depends on credentials, scopes, provider access, and tenant configuration |
| Knowledge and channels | Knowledge ingestion/search, reports, notifications, voice, RPA, and other channel features behind their required services and configuration |
| Developer access | REST API, Python SDK and CLI, TypeScript SDK, MCP server, and A2A discovery/task surfaces |
| Commerce | Merchant-scoped Shopify read-only sync, OACP artifact intake/cache, buyer-safe answers, public catalog surfaces, and prepared provider or POS handoffs |
| Operations | PostgreSQL, Redis, object storage, observability hooks, migrations, security checks, and Cloud Run deployment tooling |

A source definition is not evidence that a specific tenant has connected the provider or enabled write access. Connector actions still require approved credentials, scopes, policies, and provider availability.

## Capability status

### Implemented in the platform

- Agent definitions and tenant-created agents
- Agent and workflow execution through authenticated APIs
- Human approval queues and configurable escalation conditions
- Audit records for governed runtime activity
- Prompt templates, organization structure, and role-aware application views
- Native connector definitions and a scoped tool gateway
- Public A2A and MCP discovery with authenticated execution paths
- Python and TypeScript client packages plus an MCP server package
- Hosted billing limits and checkout integration
- OACP commerce configuration, Shopify read-only sync, artifact caching, buyer channels, and safe handoff preparation

### Configuration-dependent or provider-gated

- LLM calls require a configured supported model provider or a local model runtime.
- Each connector requires provider credentials, tenant authorization, and the relevant scopes.
- Composio is an optional integration gateway; its catalog is not the same as the native connector registry.
- RAG, voice, browser automation, outbound notifications, and local-model profiles require their supporting services.
- SSO, SCIM, enterprise support, and custom SLAs depend on plan and deployment configuration.
- WhatsApp, Telegram, payment-provider, bank, POS, and merchant-system flows require provider setup and verified callbacks or adapters.
- WooCommerce, ERP, PIM, OMS, WMS, custom commerce APIs, and additional payment rails can be recorded as adapter-ready configuration but are not represented as active execution paths until an approved adapter exists.

### Explicit non-goals and boundaries

- OACP artifacts do not create orders, reserve inventory, create mandates, capture payments, issue refunds, or prove a paid state.
- AgenticOrg does not replace the merchant, bank, payment provider, POS, or SaaS system as its source of record.
- Cached commerce evidence can support an answer or a prepared handoff; provider-owned execution must be confirmed by the provider.
- A connector listed in source is not proof of a live customer integration.
- Example workflows and editorial content are not performance guarantees.
- Security controls in the repository are not a claim of third-party certification.

## How the runtime fits together

    Browser or SDK
        |
        v
    React 19 application / FastAPI API
        |
        +-- Authentication, tenant context, roles, and scopes
        +-- Agent registry and LangGraph execution
        +-- Workflow engine, schedules, and approval queues
        +-- Tool gateway and connector adapters
        +-- Audit, evaluation, observability, and cost records
        |
        +-- PostgreSQL / Cloud SQL
        +-- Redis
        +-- GCS or compatible object storage
        +-- Configured LLM and external service providers

The managed production path runs separate API and UI services on Google Cloud Run. Artifact Registry images are commit-pinned, migrations can run before rollout, and traffic movement is explicit. Legacy Kubernetes material is not the current production path.

## OACP commerce boundary

AgenticOrg owns the buyer and seller agent runtime around OACP-backed commerce. [Grantex](https://grantex.dev) owns trust authority, protocol and policy governance, canonical artifacts, artifact verification, and adapter authority. Shopify and merchant systems remain operational sources of record. Pine Labs Plural/P3P, banks, POS systems, and payment providers own their execution rails.

The current supported source path is:

1. A merchant saves tenant-, merchant-, store-, and seller-agent-scoped configuration.
2. AgenticOrg stores Shopify credentials through the approved custody path.
3. AgenticOrg performs read-only Shopify Admin GraphQL sync.
4. Public-safe evidence is submitted to the configured Grantex authority path.
5. Issued OACP artifacts are cached with freshness, scope, risk, and revocation posture.
6. Buyer surfaces answer from valid artifacts and disclose source and freshness.
7. Purchase intent becomes a non-executing provider, merchant, or POS handoff, or a fail-closed blocker.
8. Only a verified provider or merchant callback can confirm the external outcome.

Start with [docs/oacp/README.md](docs/oacp/README.md) and the [runtime launch closure PRD](docs/oacp/runtime-launch-closure-prd.md).

## Requirements

- Python 3.12 or newer
- Node.js 20 or newer
- Docker with Docker Compose for local PostgreSQL and Redis
- At least one supported LLM provider key, or a configured local model runtime, for model-backed execution

The UI currently uses React 19. Dependency versions are declared in [pyproject.toml](pyproject.toml) and [ui/package.json](ui/package.json).

## Local development

Clone and configure the repository:

    git clone https://github.com/mishrasanjeev/agentic-org.git
    cd agentic-org
    cp .env.example .env

Install the platform and development extras:

    python -m venv .venv
    python -m pip install -e ".[v4,dev]"

Start local dependencies:

    docker compose up -d postgres redis minio

Run migrations and the API:

    python scripts/alembic_migrate.py
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

In another terminal, install and run the UI:

    cd ui
    npm ci
    npm run dev

Do not use example secrets in staging or production. Configure real secrets through the approved secret-management path.

Optional Compose profiles include local CPU/GPU model runtimes and supporting RAG or voice services. Review [docker-compose.yml](docker-compose.yml) before enabling a profile because hardware, images, and credentials vary by feature.

## Public API and protocols

All application API routes are mounted below /api/v1.

| Surface | Purpose | Authentication posture |
| --- | --- | --- |
| GET /api/v1/health | Service health | Public |
| GET /api/v1/product-facts | Live version and registry totals | Public |
| GET /api/v1/a2a/agent-card | A2A discovery card | Public |
| GET /api/v1/a2a/.well-known/agent.json | A2A discovery alias | Public |
| GET /api/v1/mcp/tools | MCP-compatible agent discovery | Public |
| Agent, workflow, tool, billing, and commerce mutations | Tenant operations | Authenticated and scoped |

Interactive OpenAPI documentation is available from the API service when enabled by the deployment. The detailed route inventory is maintained in [docs/route_inventory.json](docs/route_inventory.json), and narrative API documentation is in [docs/api-reference.md](docs/api-reference.md).

### Python SDK and CLI

See [sdk/README.md](sdk/README.md).

    pip install agenticorg
    agenticorg agents list
    agenticorg a2a card

The SDK supports authenticated platform operations exposed by its client. Server features may require a newer deployment than the installed client, so consumers should check package and server release notes.

### TypeScript SDK

See [sdk-ts/README.md](sdk-ts/README.md).

    npm install agenticorg-sdk

### MCP server

See [mcp-server/README.md](mcp-server/README.md).

    npx agenticorg-mcp-server

The MCP package exposes governed AgenticOrg agent and commerce discovery surfaces. It does not grant an MCP client unrestricted access to every connector action.

## Pricing and limits

The hosted plan source of truth is PLAN_PRICING in [core/billing/limits.py](core/billing/limits.py).

| Plan | USD list price | INR list price | Agents | Runs | Storage |
| --- | ---: | ---: | ---: | ---: | ---: |
| Free | $0/month | INR 0/month | 3 | 1,000/month | 1 GB |
| Pro | $2/month | INR 9,999/month | 15 | 10,000/month | 50 GB |
| Enterprise | $499/month | INR 49,999/month | Unlimited | Unlimited | Unlimited |

Pro also includes priority support and custom connectors. Enterprise includes 24/7 support, custom SLAs, dedicated customer success, and SSO/SCIM according to the plan definition.

The repository is Apache-2.0 licensed. Infrastructure, model-provider, external API, payment-provider, and support costs are separate from the software license.

## Security and governance

The codebase includes:

- Tenant, role, and scope checks around protected API operations
- Configurable human approvals and confidence or policy gates
- Scoped connector tools and a centralized tool gateway
- Secret-reference patterns for connector and cloud credentials
- Configurable PII redaction before model calls and in logs
- JWT verification through PyJWT with cryptographic support
- Rate limiting, security headers, and authentication-state controls
- Dependency, static-analysis, container, and regression security checks
- Audit and observability records for governed operations
- Fail-closed OACP freshness, revocation, and provider-boundary checks

Production security depends on correct deployment configuration, key custody, provider settings, tenant policies, and operational monitoring. Report vulnerabilities through [SECURITY.md](SECURITY.md); do not open a public issue for a suspected vulnerability.

## Testing

Backend:

    pytest

Focused backend checks can be run by directory or file:

    pytest tests/unit
    pytest tests/regression
    pytest tests/connector_harness

Frontend:

    cd ui
    npm ci
    npm run lint
    npm run typecheck
    npm test
    npm run build

Browser tests:

    cd ui
    npx playwright test

Playwright suites require the target application and any documented test credentials or fixtures. CI workflow files under [.github/workflows](.github/workflows) are the source of truth for which suites run on each event; the README does not claim that every E2E suite runs on every push.

## Production deployment

The current managed rollout helper targets Google Cloud Run:

    bash scripts/deploy_cloud_run.sh --sha <commit-sha> --yes

Useful modes include:

    bash scripts/deploy_cloud_run.sh --sha <commit-sha> --skip-build --with-migrations --yes
    bash scripts/deploy_cloud_run.sh --sha <commit-sha> --skip-build --traffic preserve --yes
    bash scripts/deploy_cloud_run.sh --sha <commit-sha> --dry-run

The helper stages revision-specific images, verifies image and commit metadata, supports migration-first deployment, probes health, and moves traffic according to the selected mode. Read the script help and [docs/deployment.md](docs/deployment.md) before using production credentials.

Default script configuration places Cloud Run services in asia-southeast1 and Artifact Registry in asia-south1. Both are configurable. Older Kubernetes material is retained only for historical or alternative deployment context.

## Repository map

    api/                 FastAPI application and versioned routes
    auth/                Authentication and authorization middleware
    core/                Agent runtime, models, billing, commerce, and orchestration
    connectors/          Native connector implementations and framework
    workflows/           Workflow definitions and runtime helpers
    sdk/                 Python SDK and CLI
    sdk-ts/              TypeScript SDK
    mcp-server/          MCP server package
    ui/                  React 19 application and public web assets
    migrations/          Database migrations
    observability/       Metrics, tracing, and logging helpers
    tests/               Backend unit, regression, security, and integration tests
    ui/e2e/              Playwright browser tests
    docs/                Product, architecture, operations, and protocol documentation
    scripts/             Validation, migration, release, and deployment helpers

## Search and answer-engine assets

The public site includes conventional search and answer-engine discovery assets:

- [robots.txt](https://agenticorg.ai/robots.txt)
- [sitemap.xml](https://agenticorg.ai/sitemap.xml)
- [llms.txt](https://agenticorg.ai/llms.txt)
- [llms-full.txt](https://agenticorg.ai/llms-full.txt)
- Per-route titles, descriptions, canonicals, social metadata, and structured data
- Crawlable blog, resource, product, protocol, solution, trust, and legal pages

The two llms files are generated by [ui/scripts/generate-llms.mjs](ui/scripts/generate-llms.mjs). They are supplemental machine-readable navigation documents, not replacements for crawlable HTML, accurate structured data, robots controls, or the sitemap.

To refresh the tracked sitemap and llms copies plus route JSON-LD CSP hashes:

    cd ui
    npm run seo:sync
    npm run seo:verify

## Documentation

- [Product requirements](docs/PRD.md)
- [Architecture](docs/architecture.md)
- [API reference](docs/api-reference.md)
- [Deployment](docs/deployment.md)
- [Testing guide](docs/TEST_PLAN.md)
- [OACP runtime documentation](docs/oacp/README.md)
- [Python SDK](sdk/README.md)
- [TypeScript SDK](sdk-ts/README.md)
- [MCP server](mcp-server/README.md)
- [Changelog](CHANGELOG.md)
- [Roadmap](ROADMAP.md)

Some older reports describe prior architecture or point-in-time validation. Prefer current code, canonical runbooks, and live product facts when a historical document conflicts with the running system.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Keep pull requests focused, add tests for behavior changes, and update generated public documentation when a source-of-truth field changes.

## License

AgenticOrg is licensed under the [Apache License 2.0](LICENSE).
