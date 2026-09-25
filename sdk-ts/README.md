# agenticorg-sdk

> AgenticOrg is owned by **Orchestrum Technologies LLP**. Inventor / Owner:
> **Sanjeev Kumar**. Contact [sanjeev@orchestrum.in](mailto:sanjeev@orchestrum.in)
> or [mishra.sanjeev@gmail.com](mailto:mishra.sanjeev@gmail.com). See
> [Ownership and contact](../docs/OWNERSHIP.md).

TypeScript SDK for AgenticOrg - run AI agents, generate agents from plain
English or SOPs, discover A2A/MCP tools, search knowledge, and create/run
workflows.

The npm registry currently publishes `agenticorg-sdk@0.3.0`. The `0.4.0`
source in this repository adds runtime resources, including `client.cases`;
build this checkout for evaluation until a newer package is published. A
matching backend deployment is required.

## Install

```bash
npm install agenticorg-sdk
```

## Quickstart

```typescript
import { AgenticOrg } from "agenticorg-sdk";

const client = new AgenticOrg({ apiKey: "your-key" });

// Run an agent by type (companyId is required for agent-type runs)
const result = await client.agents.run("ap_processor", {
  inputs: { invoice_id: "INV-001", vendor_id: "V-100" },
  companyId: "<company-uuid>",
});
console.log(result.status);     // "completed"
console.log(result.confidence); // 0.95
console.log(result.output);     // {...structured result...}

// Buyer/seller commerce discovery via the seller commerce agent
const commerce = await client.agents.run("commerce_sales_agent", {
  action: "buyer_discovery_preview",
  inputs: {
    merchant_id: "merchant_demo",
    query: "Show available laptop stands under Rs 3000",
  },
  companyId: "<company-uuid>",
});

// Generate any launchable AI-template agent from skills/tools/connectors context
const draftAgent = await client.agents.generate(
  "Create a contract intelligence agent that uses Confluence knowledge, " +
    "Jira issues, and vendor policy documents to review renewal risk.",
);

// Create agent from SOP
const sopDraft = await client.sop.parseText(`
  Step 1: Receive invoice from vendor
  Step 2: Validate GSTIN on GST portal
  Step 3: 3-way match with PO and GRN
  Step 4: If amount > 5L, escalate to CFO
`, "finance");

const agent = await client.sop.deploy(sopDraft.config);

// MCP tools (for ChatGPT/Claude integration)
const tools = await client.mcp.tools();

// A2A discovery
const card = await client.a2a.agentCard();
const a2aAgents = await client.a2a.agents();

// Knowledge + workflow generation
const kb = await client.knowledge.search("vendor renewal policy", { topK: 3 });
const workflowDraft = await client.workflows.generate(
  "When a contract renewal is 30 days away, search knowledge, check Jira, " +
    "ask contract_intelligence to summarize risk, then notify vendor_manager.",
);
const workflow = await client.workflows.create({
  name: "Renewal Risk Review",
  definition: workflowDraft.workflow as Record<string, unknown>,
  domain: "ops",
});
const run = await client.workflows.run(workflow.id as string, {
  payload: { vendor_id: "V-100" },
});
```

## Authentication

```typescript
// API Key (dashboard users)
new AgenticOrg({ apiKey: "your-key" });

// Grantex Grant Token (external agents). A route needs its scope in the
// grant (e.g. agents:read), exactly as for an API key; tool scopes alone get 403.
new AgenticOrg({ grantexToken: "eyJ..." });

// Environment variable
// AGENTICORG_API_KEY=... or AGENTICORG_GRANTEX_TOKEN=...
new AgenticOrg();
```

## Resources

| Resource | Methods |
|----------|---------|
| `client.agents` | `list()`, `get(id)`, `run(type, opts)`, `create(data)`, `generate(description, opts?)` |
| `client.connectors` | `list(category?)`, `get(id)` |
| `client.sop` | `parseText(text, domain?)`, `deploy(config)` |
| `client.a2a` | `agentCard()`, `agents()` |
| `client.mcp` | `tools()`, `call(name, args?)` |
| `client.cases` | `submit(application, purpose, policyId?)`, `list({state?, limit?})`, `get(caseRef)`, `investigate(caseRef)`; repository source only |
| `client.workflows` | `templates()`, `list()`, `generate(description)`, `create(opts)`, `get(id)`, `run(id, opts?)`, `getRun(id)` |
| `client.knowledge` | `search()`, `supportedTypes()`, `upload()`, `documents()`, `delete()`, `health()`, `stats()` |
| `client.voice` | `status()`, `saveConfig()`, `testConnection()`, `runtimeHealth()`, `calls()`, `placeOutboundCall()` |
| `client.rpa` | `scripts()`, `history()`, `run()` |
| `client.bridges` | `register()`, `list()`, `status()`, `route()`, `deregister()` |
| `client.commerce` | seller onboarding, Shopify sync, artifact cache, buyer ask, protocol adapters, mandate evidence, purchase/POS preparation |

```typescript
const types = await client.knowledge.supportedTypes();
const document = await client.knowledge.upload(
  new Blob([scannedDocumentBytes]),
  "scanned-invoice.pdf",
);
const products = await client.commerce.products("merchant-123");
const answer = await client.commerce.ask({
  merchant_id: "merchant-123",
  question: "Which variants are fresh and in stock?",
});
const voiceRuntime = await client.voice.runtimeHealth("agent-uuid");
```

`placeOutboundCall`, `rpa.run`, `bridges.route`, Shopify sync, and provider
verification are intentionally explicit because they may contact external
systems or incur charges. Purchase/POS helpers prepare handoffs; provider and
POS systems remain transaction authorities.

## Governed cases

The source SDK exposes only machine-safe case calls:

```typescript
const record = await client.cases.submit(
  { legal_name: "Example Ltd", jurisdiction: "GB" },
  "aml.cdd.onboarding",
);
const scheduled = await client.cases.investigate(record.case_ref as string);
const current = await client.cases.get(record.case_ref as string);
```

`investigate()` schedules work; it does not certify a completed investigation.
The backend checks tenant enablement, role registration, the local case-purpose
allowlist and delegated tool grants before provider calls. The currently
published Python Grantex SDK `0.5.1` does not enforce token-level case purpose
or per-case caps. Human-only decisions, withdrawal, screening review and
information-request approval are not exposed through this API-key/agent-token
client or the MCP agent catalog. See the [case lifecycle](../docs/governance/case-lifecycle.md)
and [SDK contract test](test/sdk-contract-smoke.mjs).

## License

Apache-2.0
