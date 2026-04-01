# agenticorg-sdk

TypeScript SDK for AgenticOrg — run AI agents, create from SOP, A2A/MCP access.

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

// Create agent from SOP
const draft = await client.sop.parseText(`
  Step 1: Receive invoice from vendor
  Step 2: Validate GSTIN on GST portal
  Step 3: 3-way match with PO and GRN
  Step 4: If amount > 5L, escalate to CFO
`, "finance");

const agent = await client.sop.deploy(draft.config);

// MCP tools (for ChatGPT/Claude integration)
const tools = await client.mcp.tools();

// A2A discovery
const card = await client.a2a.agentCard();
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
| `client.agents` | `list()`, `get(id)`, `run(type, opts)`, `create(data)` |
| `client.sop` | `parseText(text, domain?)`, `deploy(config)` |
| `client.a2a` | `agentCard()`, `agents()` |
| `client.mcp` | `tools()`, `call(name, args?)` |

## License

Apache-2.0
