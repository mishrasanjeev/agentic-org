# agenticorg-mcp-server

MCP (Model Context Protocol) server for [AgenticOrg](https://agenticorg.ai) — expose 25+ enterprise AI agents and 269 tools to any MCP-compatible client.

## Quick Start

```bash
npm install -g agenticorg-mcp-server
AGENTICORG_API_KEY=your-key agenticorg-mcp
```

Or run directly with npx:

```bash
AGENTICORG_API_KEY=your-key npx agenticorg-mcp-server
```

## Configure in ChatGPT / Claude Desktop / Cursor

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "agenticorg": {
      "command": "npx",
      "args": ["agenticorg-mcp-server"],
      "env": {
        "AGENTICORG_API_KEY": "your-api-key"
      }
    }
  }
}
```

## Available Tools

| Tool | Description |
|------|-------------|
| `list_agents` | List all AI agents, filter by domain |
| `run_agent` | Run any agent (AP Processor, Recon, Payroll, etc.) |
| `get_agent_details` | Get full agent config and capabilities |
| `create_agent_from_sop` | Parse SOP text → create new agent |
| `deploy_agent` | Deploy an agent configuration |
| `list_connectors` | List all 42 connectors and status |
| `call_connector_tool` | Call any of 269 connector tools |
| `list_mcp_tools` | Discover all available tools |
| `discover_agents_a2a` | A2A protocol agent discovery |
| `get_agent_card` | Get the public A2A Agent Card |

## Authentication

Set one of these environment variables:

- `AGENTICORG_API_KEY` — API key from your AgenticOrg dashboard (Settings → API Keys)
- `AGENTICORG_GRANTEX_TOKEN` — Grantex grant token for delegated agent access

Optional:

- `AGENTICORG_BASE_URL` — Custom base URL (default: `https://app.agenticorg.ai`)

## Example: Run an Invoice Agent from ChatGPT

Once configured, you can say in ChatGPT:

> "Use AgenticOrg to process invoice INV-2024-001 from Tata Consultancy"

ChatGPT will call the `run_agent` tool with:
```json
{
  "agent_type": "ap_processor",
  "inputs": { "invoice_id": "INV-2024-001", "vendor": "Tata Consultancy" }
}
```

## License

Apache-2.0
