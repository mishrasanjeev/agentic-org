#!/usr/bin/env node
/**
 * Auto-generate llms.txt + llms-full.txt from source-of-truth files.
 *
 * Reads from:
 *   - core/agents/{domain}/*.py          → agent_type, domain, confidence_floor
 *   - connectors/{category}/*.py         → name, category, auth_type, tool count
 *   - ui/src/pages/Pricing.tsx           → TIERS (plan names, prices, features)
 *   - ui/src/pages/blog/blogData.ts      → blog slugs & titles
 *   - ui/src/pages/resources/contentData.ts → resource slugs & titles
 *
 * Run: node scripts/generate-llms.mjs
 * Hooked into: "build" script in package.json
 */
import { readFileSync, writeFileSync, readdirSync, statSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const UI_ROOT = join(__dirname, "..");
const REPO_ROOT = join(UI_ROOT, "..");
const SITE = "https://agenticorg.ai";

// ═══════════════════════════════════════════════════════════════════════════
// 1. PARSE AGENTS from Python source
// ═══════════════════════════════════════════════════════════════════════════
function parseAgents() {
  const agentsDir = join(REPO_ROOT, "core/agents");
  const agents = [];
  const domains = ["finance", "hr", "marketing", "ops", "backoffice", "comms"];

  for (const domain of domains) {
    const domainDir = join(agentsDir, domain);
    try {
      for (const file of readdirSync(domainDir)) {
        if (!file.endsWith(".py") || file.startsWith("__")) continue;
        const src = readFileSync(join(domainDir, file), "utf-8");
        const type = src.match(/agent_type\s*=\s*"([^"]+)"/)?.[1];
        const dom = src.match(/domain\s*=\s*"([^"]+)"/)?.[1];
        const conf = src.match(/confidence_floor\s*=\s*([\d.]+)/)?.[1];
        if (type) {
          agents.push({
            type,
            domain: dom || domain,
            confidence_floor: conf ? parseFloat(conf) : 0.8,
            label: type
              .replace(/_/g, " ")
              .replace(/\b\w/g, (c) => c.toUpperCase()),
          });
        }
      }
    } catch {
      /* domain dir may not exist */
    }
  }
  return agents;
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. PARSE CONNECTORS from Python source
// ═══════════════════════════════════════════════════════════════════════════
function parseConnectors() {
  const connDir = join(REPO_ROOT, "connectors");
  const connectors = [];
  const categories = ["finance", "hr", "marketing", "ops", "comms"];

  for (const cat of categories) {
    const catDir = join(connDir, cat);
    try {
      for (const file of readdirSync(catDir)) {
        if (!file.endsWith(".py") || file.startsWith("__")) continue;
        const src = readFileSync(join(catDir, file), "utf-8");
        const name = src.match(/^\s*name\s*=\s*"([^"]+)"/m)?.[1];
        const category = src.match(/^\s*category\s*=\s*"([^"]+)"/m)?.[1];
        const authType = src.match(/^\s*auth_type\s*=\s*"([^"]+)"/m)?.[1];
        // Count tool registrations
        const tools = [
          ...src.matchAll(/self\._tool_registry\["([^"]+)"\]/g),
        ].map((m) => m[1]);
        if (name) {
          connectors.push({
            name,
            category: category || cat,
            auth_type: authType || "api_key",
            tool_count: tools.length,
            tools,
            label: name
              .replace(/_/g, " ")
              .replace(/\b\w/g, (c) => c.toUpperCase()),
          });
        }
      }
    } catch {
      /* category dir may not exist */
    }
  }
  return connectors;
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. PARSE PRICING from Pricing.tsx
// ═══════════════════════════════════════════════════════════════════════════
function parsePricing() {
  const src = readFileSync(
    join(UI_ROOT, "src/pages/Pricing.tsx"),
    "utf-8",
  );
  const tiers = [];
  // Match each tier block
  const tierBlocks = src.match(
    /\{\s*name:\s*"[^"]+",[\s\S]*?features:\s*\[[\s\S]*?\]\s*,?\s*\}/g,
  );
  if (!tierBlocks) return tiers;
  for (const block of tierBlocks) {
    const name = block.match(/name:\s*"([^"]+)"/)?.[1];
    const price = block.match(/price:\s*"([^"]+)"/)?.[1];
    const period = block.match(/period:\s*"([^"]*)"/)?.[1] || "";
    // Extract only the features array content
    const featBlock = block.match(/features:\s*\[([\s\S]*?)\]/)?.[1] || "";
    const features = [...featBlock.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
    if (name) tiers.push({ name, price, period, features });
  }
  return tiers;
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. PARSE BLOG POSTS from blogData.ts
// ═══════════════════════════════════════════════════════════════════════════
function parseBlogPosts() {
  const src = readFileSync(
    join(UI_ROOT, "src/pages/blog/blogData.ts"),
    "utf-8",
  );
  const posts = [];
  const slugs = [...src.matchAll(/slug:\s*"([^"]+)"/g)];
  const titles = [...src.matchAll(/title:\s*"([^"]+)"/g)];
  for (let i = 0; i < slugs.length; i++) {
    posts.push({ slug: slugs[i][1], title: titles[i]?.[1] || slugs[i][1] });
  }
  return posts;
}

// ═══════════════════════════════════════════════════════════════════════════
// 5. PARSE RESOURCES from contentData.ts
// ═══════════════════════════════════════════════════════════════════════════
function parseResources() {
  const src = readFileSync(
    join(UI_ROOT, "src/pages/resources/contentData.ts"),
    "utf-8",
  );
  const pages = [];
  const slugs = [...src.matchAll(/slug:\s*"([^"]+)"/g)];
  const titles = [...src.matchAll(/title:\s*"([^"]+)"/g)];
  for (let i = 0; i < slugs.length; i++) {
    pages.push({ slug: slugs[i][1], title: titles[i]?.[1] || slugs[i][1] });
  }
  return pages;
}

// ═══════════════════════════════════════════════════════════════════════════
// 6. GROUP HELPERS
// ═══════════════════════════════════════════════════════════════════════════
function groupBy(arr, key) {
  return arr.reduce((acc, item) => {
    (acc[item[key]] = acc[item[key]] || []).push(item);
    return acc;
  }, {});
}

const DOMAIN_LABELS = {
  finance: "Finance",
  hr: "HR",
  marketing: "Marketing",
  ops: "Operations",
  backoffice: "Back Office",
  comms: "Communications & Cloud",
};

const DOMAIN_ROLES = {
  finance: "CFO",
  hr: "CHRO",
  marketing: "CMO",
  ops: "COO",
  backoffice: "COO",
  comms: "CTO",
};

// ═══════════════════════════════════════════════════════════════════════════
// 7. GENERATE llms.txt (summary)
// ═══════════════════════════════════════════════════════════════════════════
function generateLlmsTxt(agents, connectors, pricing, blogs, resources) {
  const totalTools = connectors.reduce((s, c) => s + c.tool_count, 0);
  const agentsByDomain = groupBy(agents, "domain");
  const connsByCategory = groupBy(connectors, "category");

  const agentList = agents.map((a) => a.label).join(", ");
  const indiaConnectors = connectors
    .filter((c) =>
      ["gstn", "epfo", "darwinbox", "tally", "income_tax_india", "banking_aa"].includes(c.name),
    )
    .map((c) => c.label)
    .join(", ");

  const pricingLines = pricing
    .map((t) => {
      const feat = t.features.slice(0, 3).join(", ");
      return `- ${t.name} (${t.price}${t.period}): ${feat}`;
    })
    .join("\n");

  return `# AgenticOrg — AI Virtual Employees for Enterprise

> AgenticOrg lets you create AI virtual employees or deploy ${agents.length} pre-built agents across 6 domains — Finance, HR, Marketing, Operations, Back Office, and Communications — with human-in-the-loop governance on every critical decision.

## What is AgenticOrg?

AgenticOrg is an enterprise AI virtual employee platform. Create custom AI agents with names, personas, and tailored instructions through a no-code wizard — or deploy ${agents.length} pre-built agents that automate back-office operations across 6 domains: Finance, HR, Marketing, Operations, Back Office, and Communications & Cloud. Each agent connects to ${connectors.length} enterprise systems (${totalTools} tools) and executes domain-specific tasks with human approval on every critical decision.

## Key Features

- **AI Virtual Employees**: Create custom AI agents with employee names, designations, and specializations. Multiple agents can share the same role with smart routing
- **No-Code Agent Creator**: 5-step wizard to build agents — set persona, pick role, configure prompt from templates, define behavior, and deploy
- **${agents.length} Pre-Built AI Agents**: ${agentList}
- **${connectors.length} Enterprise Connectors (${totalTools} tools)**: ${connectors.map((c) => `${c.label} (${c.tool_count} tools)`).join(", ")}
- **Agents That Act**: Agents don't just generate text — they call real external APIs via a tool_calls pipeline: LLM reasons → outputs tool calls → Tool Gateway executes → results fed back
- **Human-in-the-Loop (HITL)**: Every critical decision requires human approval. Prompts are locked after agent promotion — clone to edit
- **Shadow Mode**: Test agents against real data before promoting to production
- **Role-Based Access Control**: Domain isolation per role (CEO, CFO, CHRO, CMO, COO, Auditor)
- **Agent Observatory**: Real-time monitoring of agent reasoning traces, tool calls, confidence scores
- **Per-Agent LLM Selection**: Gemini 2.5 Flash (default), Claude 3.5 Sonnet, or GPT-4o
- **Shadow Limit Enforcement**: Agents in shadow mode have configurable sample limits, accuracy floors, and 6 quality gates that must all pass before promotion to production
- **MCP Registry Listed**: AgenticOrg is listed in the official MCP Registry — discoverable by any MCP-compatible client (ChatGPT, Claude Desktop, Cursor, Windsurf)

## Agents by Department

${Object.entries(agentsByDomain)
  .map(
    ([dom, agts]) =>
      `### ${DOMAIN_LABELS[dom] || dom} (${agts.length} agents, ${DOMAIN_ROLES[dom] || "COO"} oversight)\n${agts.map((a) => `- ${a.label} (confidence: ${a.confidence_floor})`).join("\n")}`,
  )
  .join("\n\n")}

## India-First Connectors

AgenticOrg is built for Indian enterprise with native connectors for: ${indiaConnectors}

## Pricing

${pricingLines}

## Developer SDKs & Integration

- **Python SDK**: \`pip install agenticorg\` — run agents, parse SOPs, A2A/MCP access ([PyPI](https://pypi.org/project/agenticorg/))
- **TypeScript SDK**: \`npm i agenticorg-sdk\` — full TypeScript client library ([npm](https://www.npmjs.com/package/agenticorg-sdk))
- **MCP Server**: \`npx agenticorg-mcp-server\` — expose 50+ agents and 340+ native tools to ChatGPT, Claude Desktop, Cursor ([npm](https://www.npmjs.com/package/agenticorg-mcp-server))
- **CLI**: \`agenticorg agents list\`, \`agenticorg agents run\`, \`agenticorg sop deploy\`
- **API Keys**: Generate from Settings > API Keys in the dashboard. Keys use \`ao_sk_\` prefix, bcrypt-hashed, scoped, revocable
- **A2A Protocol**: Google Agent-to-Agent discovery via Agent Cards at \`/api/v1/a2a/agent-card\`
- **MCP Protocol**: Anthropic Model Context Protocol — 10 tools for agent management and connector access
- **Grantex Authorization**: Delegated auth with scoped grant tokens for third-party agent access

## Links

- Website: ${SITE}
- Playground (try live): ${SITE}/playground
- Pricing: ${SITE}/pricing
- Evaluation Matrix: ${SITE}/evals
- Integration Workflow: ${SITE}/integration-workflow
- Blog: ${SITE}/blog
${blogs.map((b) => `  - ${b.title}: ${SITE}/blog/${b.slug}`).join("\n")}
- Resources: ${SITE}/resources
${resources.map((r) => `  - ${r.title}: ${SITE}/resources/${r.slug}`).join("\n")}
- GitHub: https://github.com/mishrasanjeev/agentic-org
- Python SDK (PyPI): https://pypi.org/project/agenticorg/
- TypeScript SDK (npm): https://www.npmjs.com/package/agenticorg-sdk
- MCP Server (npm): https://www.npmjs.com/package/agenticorg-mcp-server
`;
}

// ═══════════════════════════════════════════════════════════════════════════
// 8. GENERATE llms-full.txt (detailed)
// ═══════════════════════════════════════════════════════════════════════════
function generateLlmsFullTxt(agents, connectors, pricing, blogs, resources) {
  const totalTools = connectors.reduce((s, c) => s + c.tool_count, 0);
  const agentsByDomain = groupBy(agents, "domain");
  const connsByCategory = groupBy(connectors, "category");

  const pricingSections = pricing
    .map((t) => {
      const features = t.features.map((f) => `- ${f}`).join("\n");
      return `### ${t.name} (${t.price}${t.period})\n${features}`;
    })
    .join("\n\n");

  const connectorSections = Object.entries(connsByCategory)
    .map(([cat, conns]) => {
      const label = DOMAIN_LABELS[cat] || cat;
      const lines = conns
        .map((c) => `- ${c.label}: ${c.tool_count} tools (${c.auth_type})`)
        .join("\n");
      return `### ${label} (${conns.length} connectors)\n${lines}`;
    })
    .join("\n\n");

  const agentSections = Object.entries(agentsByDomain)
    .map(([dom, agts]) => {
      const label = DOMAIN_LABELS[dom] || dom;
      const role = DOMAIN_ROLES[dom] || "COO";
      const lines = agts
        .map(
          (a) =>
            `#### ${a.label} (${a.type})\n- Domain: ${label}\n- Confidence floor: ${(a.confidence_floor * 100).toFixed(0)}%`,
        )
        .join("\n\n");
      return `### ${label} Domain (${agts.length} agents, ${role} oversight)\n\n${lines}`;
    })
    .join("\n\n");

  return `# AgenticOrg — Complete Product Documentation for LLMs

> AgenticOrg is an enterprise AI virtual employee platform. Create custom AI agents with names, personas, and tailored instructions — or deploy ${agents.length} pre-built agents across 6 domains: Finance, HR, Marketing, Operations, Back Office, and Communications. Each agent automates domain-specific tasks with human-in-the-loop (HITL) governance, connecting to ${connectors.length} enterprise systems (${totalTools} tools). Built for Indian enterprise with native GSTN, EPFO, and Darwinbox connectors.

## Product Overview

AgenticOrg replaces manual back-office work with AI virtual employees that reason, execute, and escalate. Admins can create custom agents through a no-code wizard or deploy pre-built agents. Every critical decision requires human approval. The platform is role-based: CFOs see only finance agents, CHROs see only HR agents, etc.

Website: ${SITE}
Playground (try live): ${SITE}/playground
Pricing: ${SITE}/pricing
Evaluation Matrix: ${SITE}/evals
GitHub: https://github.com/mishrasanjeev/agentic-org

---

## Architecture

8-layer stack:
1. API Gateway (FastAPI, JWT auth, rate limiting)
2. Orchestration (Nexus orchestrator, DAG workflows, parallel execution)
3. Agent Runtime (LLM reasoning via Gemini 2.5 Flash, tool calling, confidence scoring)
4. HITL Governance (approval queues, role-based escalation, timeout policies)
5. Connector Hub (${connectors.length} pre-built connectors with circuit breakers)
6. Schema Registry (validated data schemas)
7. Observability (Prometheus metrics, OpenTelemetry traces, structured logging)
8. Infrastructure (GKE Autopilot, Cloud SQL PostgreSQL, Redis)

---

## Virtual Employee System

AgenticOrg treats AI agents as virtual employees — named personas that admins create, train (via prompts), and deploy.

### Agent Creator (No-Code Wizard)
5-step wizard available to admin/CEO users:
1. **Persona**: Employee name, designation, avatar, domain
2. **Role**: Agent type (pick from ${agents.length} built-in or create custom), specialization, routing filters
3. **Prompt**: Select from production-tested prompt templates or write custom instructions, fill in template variables
4. **Behavior**: Confidence floor, HITL conditions, LLM model, max retries
5. **Review**: Summary and deploy as Shadow

### Per-Agent LLM Selection
Each agent can specify its preferred LLM model: Gemini 2.5 Flash (default, free tier), Claude 3.5 Sonnet (Anthropic), or GPT-4o (OpenAI). The system checks if the required API key is configured before using the specified model — if not, it safely falls back to the global default.

### Org Chart Hierarchy
Agents can report to other agents via parent_agent_id. When an agent fails or triggers HITL, the task escalates to the parent agent, creating management chains.

### Per-Agent Budget Enforcement
Each agent can have monthly cost caps. Before every execution, the system checks cumulative monthly spend. If exceeded, the agent returns E1008 (budget_exceeded) without making an LLM call.

---

## All ${agents.length} Agents — Detailed

${agentSections}

---

## ${connectors.length} Enterprise Connectors (${totalTools} total tools)

${connectorSections}

---

## Human-in-the-Loop (HITL) Governance

Every agent has configurable HITL conditions. When triggered, the agent stops execution and creates an approval item. The relevant department head sees the item with full context and can approve, reject, or defer.

### HITL Trigger Types
- amount_threshold: Transaction amount exceeds configured limit
- confidence_below_floor: Agent confidence below minimum threshold
- regulatory_filing: All government filings (zero auto-file policy)
- break_threshold: Reconciliation break above limit
- anomaly_detection: Unexpected pattern detected
- budget_approval: Spend above approval threshold
- incident_escalation: High-severity incident requiring acknowledgment

### HITL Flow
1. Agent detects trigger condition
2. Creates HITL item with priority (critical/high/normal/low)
3. Assigns to role-based queue (cfo/chro/cmo/coo)
4. Department head reviews context and decides (approve/reject/defer)
5. Agent resumes or stops based on decision
6. Full decision audit trail with timestamp, decision, notes

---

## Shadow Mode & Quality Gates

Before promoting an agent to production, it runs in "shadow mode" — processing real data without taking action. The Shadow Comparator evaluates across 6 quality gates:

1. Output Accuracy (threshold >= 0.90)
2. Confidence Calibration (Pearson r >= 0.70)
3. HITL Rate Comparison (tolerance +/-5pp)
4. Hallucination Detection (zero tolerance)
5. Tool Error Rate (< 2%)
6. Latency Comparison (<= 1.3x reference P95)

All 6 gates must pass for promotion.

### Shadow Limit Enforcement
Shadow agents have configurable limits: minimum sample count (default 100), accuracy floor (default 0.95), and maximum shadow duration. If an agent exceeds shadow_max_runs without passing all 6 gates, it is automatically paused and flagged for review. This prevents indefinite shadow execution and ensures agents are either promoted or retired.

---

## MCP Registry

AgenticOrg is listed in the official MCP Registry (mcpregistry.com) under the name \`agenticorg-mcp-server\`. Any MCP-compatible client — ChatGPT, Claude Desktop, Cursor, Windsurf, VS Code — can discover and connect to AgenticOrg agents and tools automatically. The MCP server exposes 10 tools and ${totalTools} connector tools via stdio transport.

---

## Role-Based Access Control

| Role | Sees | Can Write | HITL Queue |
|------|------|-----------|------------|
| CEO/Admin | All domains | Everything | All approvals |
| CFO | Finance agents | Finance domain | Finance approvals |
| CHRO | HR agents | HR domain | HR approvals |
| CMO | Marketing agents | Marketing domain | Marketing approvals |
| COO | Operations agents | Ops domain | Ops approvals |
| Auditor | Audit log (all) | Nothing (read-only) | None |

---

## Pricing

${pricingSections}

---

## Security

- Encryption at rest (Google-managed, Cloud SQL)
- Encryption in transit (HTTPS/TLS)
- JWT authentication with RS256/HS256
- bcrypt password hashing (12 rounds)
- Token revocation (logout blacklist)
- Rate limiting (10 auth failures = 15-min block, 5 signups/IP/hour)
- PII masking (email, Aadhaar, PAN, phone auto-masked in audit logs)
- Tenant isolation (Row-Level Security)
- RBAC (6 roles, domain-scoped)
- Automated daily database backups (7 retained)
- Complete audit trail (7-year retention)
- Security headers: HSTS, CSP, X-Frame-Options DENY

---

## Technology Stack

- Backend: Python 3.12, FastAPI, SQLAlchemy (async), asyncpg
- Frontend: React 18, TypeScript, Vite, Tailwind CSS, recharts
- Database: PostgreSQL 16 (Cloud SQL), Redis 7
- LLM: Gemini 2.5 Flash (default), Claude, GPT-4o (configurable)
- Infrastructure: GKE Autopilot (asia-south1), Cloud SQL, Artifact Registry
- CI/CD: GitHub Actions (lint → test → build → deploy)
- Monitoring: Prometheus, OpenTelemetry, structlog
- License: Apache 2.0

---

## Blog

${blogs.map((b) => `- [${b.title}](${SITE}/blog/${b.slug})`).join("\n")}

## Resources

${resources.map((r) => `- [${r.title}](${SITE}/resources/${r.slug})`).join("\n")}

---

## Developer SDKs & Integration

### Python SDK (PyPI)
- Install: \`pip install agenticorg\`
- Usage: \`from agenticorg import AgenticOrg; client = AgenticOrg(api_key="ao_sk_...")\`
- Features: Run agents, parse SOPs, A2A discovery, MCP tool calls, CLI
- Package: https://pypi.org/project/agenticorg/

### TypeScript SDK (npm)
- Install: \`npm i agenticorg-sdk\`
- Usage: \`import { AgenticOrg } from "agenticorg-sdk"; const client = new AgenticOrg({ apiKey: "ao_sk_..." })\`
- Features: Agents, SOPs, A2A, MCP — full TypeScript types
- Package: https://www.npmjs.com/package/agenticorg-sdk

### MCP Server (npm)
- Install: \`npx agenticorg-mcp-server\`
- Exposes 10 tools: list_agents, run_agent, get_agent_details, create_agent_from_sop, deploy_agent, list_connectors, call_connector_tool, list_mcp_tools, discover_agents_a2a, get_agent_card
- Works with ChatGPT, Claude Desktop, Cursor, Windsurf, or any MCP client
- Package: https://www.npmjs.com/package/agenticorg-mcp-server

### API Keys
- Generate from dashboard: Settings > API Keys
- Format: \`ao_sk_{40 hex chars}\` — bcrypt-hashed, scoped, revocable
- Scopes: agents:read, agents:run, connectors:read, mcp:read, mcp:call, a2a:read
- Max 10 active keys per organization
- Auth: \`Authorization: Bearer ao_sk_...\`

### Integration Protocols
- **A2A (Agent-to-Agent)**: Google's protocol for agent discovery. Agent Card at \`/api/v1/a2a/agent-card\`
- **MCP (Model Context Protocol)**: Anthropic's protocol. ${totalTools} tools exposed via stdio transport. Listed in official MCP Registry
- **Grantex**: Delegated authorization with RS256 grant tokens for cross-tenant agent access

---

## Contact

- Website: ${SITE}
- Book a demo: ${SITE} (click "Book a Demo")
- Integration Workflow: ${SITE}/integration-workflow
- GitHub: https://github.com/mishrasanjeev/agentic-org
- Python SDK: https://pypi.org/project/agenticorg/
- TypeScript SDK: https://www.npmjs.com/package/agenticorg-sdk
- MCP Server: https://www.npmjs.com/package/agenticorg-mcp-server
`;
}

// ═══════════════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════════════
const agents = parseAgents();
const connectors = parseConnectors();
const pricing = parsePricing();
const blogs = parseBlogPosts();
const resources = parseResources();
const totalTools = connectors.reduce((s, c) => s + c.tool_count, 0);

const llmsTxt = generateLlmsTxt(agents, connectors, pricing, blogs, resources);
const llmsFullTxt = generateLlmsFullTxt(
  agents,
  connectors,
  pricing,
  blogs,
  resources,
);

writeFileSync(join(UI_ROOT, "public/llms.txt"), llmsTxt, "utf-8");
writeFileSync(join(UI_ROOT, "public/llms-full.txt"), llmsFullTxt, "utf-8");

console.log(
  `llms.txt + llms-full.txt generated — ${agents.length} agents, ${connectors.length} connectors (${totalTools} tools), ${pricing.length} tiers, ${blogs.length} blogs, ${resources.length} resources`,
);
