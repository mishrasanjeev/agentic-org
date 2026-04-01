# AgenticOrg Roadmap

## Current (v2.2) — Shipped

- 25 pre-built AI agents across 5 domains + 37 custom agents on demo
- **Agent-to-Connector Bridge** — agents reason with LLM then execute real API calls via tool_calls
- **3 live connectors verified on production**: GitHub (9 tools), Jira (11 tools), HubSpot (13 tools)
- **3 pre-built workflows**: Incident Response, Lead-to-Revenue, Weekly DevOps Health Report
- **Workflow engine executes real agents** (was stub, now runs LLM + tools)
- Virtual Employee System (names, personas, specializations)
- No-code Agent Creator wizard
- 43 enterprise connectors with 273 tools
- 27 prompt templates with audit trail
- HITL governance with configurable thresholds
- Per-agent LLM selection (Gemini/Claude/GPT-4o)
- Org chart hierarchy (parent-child, smart escalation, CSV import)
- Per-agent budget enforcement with auto-pause
- Sales Agent with automated pipeline + Gmail integration
- 39-page SEO content (blog + resources across 7 topic clusters)
- **1,031 automated tests + 125/125 production E2E (100%)**
- GKE deployment with zero-downtime rolling updates

## Previous (v2.1) — Shipped 2026-03-21

- Initial platform with 24 agents, workflow engine, 43 connectors
- Shadow deployment with 6 quality gates
- Full PostgreSQL DDL, RLS, CI/CD pipeline

## Next (v2.3) — In Progress

### More Live Connector Integrations
- [ ] Stripe test mode (payment processing)
- [ ] Salesforce developer account
- [ ] Slack (real workspace integration)
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
