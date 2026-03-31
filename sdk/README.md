# AgenticOrg Python SDK

Run AI agents, create agents from SOPs, and manage connectors — all from Python or the terminal.

## Install

```bash
pip install agenticorg
```

## Quickstart (Python)

```python
from agenticorg import AgenticOrg

client = AgenticOrg(api_key="your-key")

# List agents
agents = client.agents.list(domain="finance")

# Run an agent
result = client.agents.run("ap_processor", inputs={
    "invoice_id": "INV-001",
    "vendor_id": "V-100",
})
print(result["status"])      # "completed"
print(result["confidence"])  # 0.95
print(result["output"])      # {...structured result...}

# Create agent from SOP
draft = client.sop.parse_text("""
Step 1: Receive invoice from vendor
Step 2: Validate GSTIN on GST portal
Step 3: 3-way match with PO and GRN
Step 4: If amount > 5L, escalate to CFO
Step 5: Post journal entry
""", domain_hint="finance")

# Review and deploy
agent = client.sop.deploy(draft["config"])
print(agent["agent_id"])  # deployed as shadow agent
```

## Quickstart (CLI)

```bash
# Set your API key
export AGENTICORG_API_KEY=your-key

# List agents
agenticorg agents list
agenticorg agents list --domain finance

# Run an agent
agenticorg agents run ap_processor --input '{"invoice_id": "INV-001"}'

# Parse SOP and create agent
agenticorg sop parse --file invoice_sop.pdf --domain finance

# View A2A agent card
agenticorg a2a card

# List MCP tools (for ChatGPT/Claude)
agenticorg mcp tools
```

## Authentication

Three options:

1. **API Key** (dashboard users): `AgenticOrg(api_key="your-key")`
2. **Grantex Grant Token** (external agents): `AgenticOrg(grantex_token="eyJ...")`
3. **Environment variable**: `AGENTICORG_API_KEY` or `AGENTICORG_GRANTEX_TOKEN`

## Resources

| Resource | Methods |
|----------|---------|
| `client.agents` | `list()`, `get(id)`, `run(type, inputs=...)`, `create(...)` |
| `client.connectors` | `list()`, `get(id)` |
| `client.sop` | `parse_text(text)`, `upload(file)`, `deploy(config)` |
| `client.a2a` | `agent_card()`, `agents()` |
| `client.mcp` | `tools()`, `call(name, args)` |

## License

Apache-2.0
