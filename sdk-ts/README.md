# agenticorg-sdk

TypeScript SDK for AgenticOrg - run AI agents, generate agents from plain
English or SOPs, discover A2A/MCP tools, search knowledge, and create/run
workflows.

## Install

```bash
npm install agenticorg-sdk
```

## Quickstart

```typescript
import { AgenticOrg } from "agenticorg-sdk";

const client = new AgenticOrg({ apiKey: "your-key" });

// Run an agent
const result = await client.agents.run("ap_processor", {
  inputs: { invoice_id: "INV-001", vendor_id: "V-100" },
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

// Grantex Grant Token (external agents)
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
| `client.workflows` | `templates()`, `list()`, `generate(description)`, `create(opts)`, `get(id)`, `run(id, opts?)`, `getRun(id)` |
| `client.knowledge` | `search(query, { topK })` |

## License

Apache-2.0
