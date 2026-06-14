# agenticorg-mcp-server

MCP (Model Context Protocol) server for [AgenticOrg](https://agenticorg.ai). It exposes AgenticOrg AI agents, A2A discovery, SOP parsing/deploy, connector discovery, and platform MCP tool discovery to any MCP-compatible client.

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

### Config file paths by platform

| Client | macOS | Windows |
|--------|-------|---------|
| **Claude Desktop** | `~/Library/Application Support/Claude/claude_desktop_config.json` | `%APPDATA%\Claude\claude_desktop_config.json` |
| **ChatGPT Desktop** | Currently in beta; config path may change. Check OpenAI docs for the latest. | Currently in beta; config path may change. |
| **Cursor** | `.cursor/mcp.json` in your project root | `.cursor/mcp.json` in your project root |

## Available Tools

| Tool | Description |
|------|-------------|
| `list_agents` | List all AI agents, filter by domain |
| `run_agent` | Run any agent, including `commerce_sales_agent` |
| `get_agent_details` | Get full agent config and capabilities |
| `create_agent_from_sop` | Parse SOP text and create a draft agent |
| `deploy_agent` | Deploy an agent configuration |
| `list_connectors` | List native connectors and status for discovery |
| `list_mcp_tools` | Discover AgenticOrg agent tools exposed by the platform MCP API |
| `discover_agents_a2a` | A2A protocol agent discovery |
| `get_agent_card` | Get the public A2A Agent Card |

Connector tools are not exposed as direct MCP tools. Use `run_agent` for the agent that owns the connector workflow, or run `list_mcp_tools` to see platform agent tools such as `agenticorg_commerce_sales_agent`.

## Authentication

Set one of these environment variables:

- `AGENTICORG_API_KEY`: API key from your AgenticOrg dashboard (Settings > API Keys)
- `AGENTICORG_GRANTEX_TOKEN`: Grantex grant token for delegated agent access

Optional:

- `AGENTICORG_BASE_URL`: custom API base URL (default: `https://app.agenticorg.ai`)

## Example: Run a Commerce Agent from ChatGPT

Once configured, you can say in ChatGPT:

> "Use AgenticOrg to show a read-only buyer discovery preview for merchant_demo."

ChatGPT will call the `run_agent` tool with:

```json
{
  "agent_type": "commerce_sales_agent",
  "action": "buyer_discovery_preview",
  "inputs": {
    "merchant_id": "merchant_demo",
    "request_text": "show a read-only buyer discovery preview"
  }
}
```

## Example: Create an Agent from SOP via ChatGPT

You can create a new agent draft directly from a standard operating procedure:

> "Use AgenticOrg to create an agent from this SOP: Step 1 - Receive vendor invoice. Step 2 - Validate GSTIN. Step 3 - 3-way match with PO and GRN. Step 4 - If amount > 5L, escalate to CFO."

ChatGPT will call the `create_agent_from_sop` tool with:

```json
{
  "sop_text": "Step 1: Receive vendor invoice\nStep 2: Validate GSTIN on GST portal\nStep 3: 3-way match with PO and GRN\nStep 4: If amount > 5L, escalate to CFO",
  "domain_hint": "finance"
}
```

The server parses the SOP, generates an agent configuration, and returns the draft for review before deployment.

## Troubleshooting

**"AGENTICORG_API_KEY not set" error**

Make sure the `env` block in your MCP config includes the key, or export it in your shell before running:

```bash
export AGENTICORG_API_KEY=your-api-key
```

**"Connection refused" or server not starting**

- Verify Node.js >= 18 is installed: `node --version`
- Try running manually first: `AGENTICORG_API_KEY=your-key npx agenticorg-mcp-server`
- Check that no firewall or proxy is blocking local stdio communication

**"Tool not found" when calling a connector tool**

- Direct connector tools are not exposed by this MCP server.
- Use `run_agent` for the agent that owns the connector workflow.
- Run `list_mcp_tools` to see agent tools exposed by the platform.

**Claude Desktop / Cursor not detecting the server**

- Ensure the config JSON is valid.
- Restart the client after editing the config file.
- Check the client's MCP log for error messages.

## License

Apache-2.0
