# AgenticOrg Roadmap

## Current (v2.3) — Shipped 2026-03-31

- 25 pre-built AI agents across **6 domains** (28 total skills incl. 3 comms agent types)
- **Agent-to-Connector Bridge** — agents reason with LLM then execute real API calls via tool_calls
- **3 live connectors verified on production**: GitHub (9 tools), Jira (11 tools), HubSpot (13 tools)
- **3 pre-built workflows**: Incident Response, Lead-to-Revenue, Weekly DevOps Health Report
- **Workflow engine executes real agents** (was stub, now runs LLM + tools)
- **Python SDK** (`pip install agenticorg`) + **TypeScript SDK** (`npm i agenticorg-sdk`)
- **MCP Server** (`npx agenticorg-mcp-server`) — 340+ tools exposed to Claude Desktop, Cursor, ChatGPT
- **CLI** — `agenticorg agents list/run`, `agenticorg sop parse`, `agenticorg mcp tools`
- **API Key Management** — `ao_sk_` prefix, admin-only, bcrypt-hashed
- **A2A Protocol** — agent discovery card + cross-platform task execution
- **Grantex Authorization** — delegated scopes, token pool with auto-refresh
- **HITL via GraphInterrupt** — LangGraph-based pause/resume at approval nodes
- **Shadow Limit Enforcement** — quality gates before promotion
- **Connector Registry Endpoint** — `GET /connectors/registry`
- **Secret Manager** — GCP Secret Manager integration for connector credentials
- **Tool Validation** — scope enforcement on all agent tool calls
- **Auth Failure Clearing** — IP-based tracking with auto-block + success clearing
- Virtual Employee System (names, personas, specializations)
- No-code Agent Creator wizard
- 43 enterprise connectors with 340+ tools
- 27 prompt templates with audit trail
- HITL governance with configurable thresholds
- Per-agent LLM selection (Gemini/Claude/GPT-4o)
- Org chart hierarchy (parent-child, smart escalation, CSV import)
- Per-agent budget enforcement with auto-pause
- Sales Agent with automated pipeline + Gmail integration
- Integration Workflow page (`/integration-workflow`)
- Developer section on landing page
- 39-page SEO content (blog + resources across 7 topic clusters)
- **1,196+ automated tests + 148/148 production E2E (100%)**
- GKE deployment with zero-downtime rolling updates

## Previous (v2.2) — Shipped 2026-03-29

- Agent-to-connector bridge, tool calling pipeline
- GitHub/Jira/HubSpot live connectors verified
- Workflow engine real execution (replaced stubs)
- Token blacklist fix, playground 401 fix, agent promote fix
- 1,031 automated tests + 125/125 production E2E

## Previous (v2.1) — Shipped 2026-03-21

- Initial platform with 24 agents, workflow engine, 54 connectors
- Shadow deployment with 6 quality gates
- Full PostgreSQL DDL, RLS, CI/CD pipeline

## Next (v2.4) — In Progress

### More Live Connector Integrations
- [ ] Stripe test mode (payment processing)
- [ ] Salesforce developer account
- [ ] Google Calendar (workspace scheduling)

### Server-Side Rendering
- [ ] Pre-render key pages for Google/AI crawlers
- [ ] Evaluate Next.js migration vs. Prerender.io

### Agent Marketplace
- [ ] Publish/discover community-built agents
- [ ] Template sharing
- [ ] One-click agent installation

### Workflow Builder UI
- [ ] Visual drag-and-drop workflow editor
- [ ] Conditional branching visualization
- [ ] Real-time workflow monitoring

## Future (v3.0) — Planned

### Multi-Agent Collaboration
- Agent-to-agent communication (not just orchestrator-mediated)
- Shared memory/context between agents in a team
- Conflict resolution for competing agent recommendations

### Advanced Governance
- Policy-as-code (OPA integration for agent permissions)
- Automated compliance reporting (SOC-2 Type II)
- Multi-region data residency with automatic routing

### Enterprise Features
- SSO (SAML, OIDC) for enterprise identity providers
- Custom branding / white-label
- Dedicated tenant infrastructure (not shared)
- SLA monitoring with automated alerting

### India Deepening
- Regional language support (Hindi, Tamil, Telugu, Kannada) for invoice OCR
- UPI payment integration
- SEBI compliance for financial services agents
- DigiLocker integration for document verification

## How to Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines. Roadmap items marked with `[ ]` are open for community contribution.

Suggest features or vote on priorities in [GitHub Discussions](https://github.com/mishrasanjeev/agentic-org/discussions).
