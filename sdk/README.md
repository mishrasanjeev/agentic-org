# AgenticOrg Python SDK

Run AI agents, generate agents from plain English or SOPs, discover A2A/MCP
tools, search knowledge, and create/run workflows from Python or the terminal.

## Install

```bash
pip install agenticorg
```

## Quickstart (Python)

```python
from agenticorg import AgenticOrg, AgentRunResult

client = AgenticOrg(api_key="your-key")

# List agents
agents = client.agents.list(domain="finance")

# Run an agent
result: AgentRunResult = client.agents.run("ap_processor", inputs={
    "invoice_id": "INV-001",
    "vendor_id": "V-100",
})
print(result.status)      # "completed"
print(result.confidence)  # 0.95
print(result.output)      # {...structured result...}

# Buyer/seller commerce discovery via the seller commerce agent
commerce: AgentRunResult = client.agents.run(
    "commerce_sales_agent",
    action="buyer_discovery_preview",
    inputs={
        "merchant_id": "merchant_demo",
        "query": "Show available laptop stands under Rs 3000",
    },
)
print(commerce.status, commerce.output)

# Generate any launchable AI-template agent from skills/tools/connectors context
draft_agent = client.agents.generate(
    "Create a contract intelligence agent that uses Confluence knowledge, "
    "Jira issues, and vendor policy documents to review renewal risk.",
)
print(draft_agent["suggestions"][0]["agent_type"])

# Create an agent directly
agent = client.agents.create(
    name="Invoice Validator - GST Specialist",
    agent_type="invoice_validator_gst",
    domain="finance",
    llm_model="claude-3-5-sonnet-20241022",
    confidence_floor=0.90,
    hitl_condition="total > 500000 OR einvoice_failed==true",
    authorized_tools=[
        "oracle_fusion:read:purchase_order",
        "gstn_api:read:validate_gstin",
    ],
    initial_status="shadow",
)
print(agent["agent_id"])     # new agent UUID
print(agent["status"])       # "shadow"

# Create agent from SOP
sop_draft = client.sop.parse_text("""
Step 1: Receive invoice from vendor
Step 2: Validate GSTIN on GST portal
Step 3: 3-way match with PO and GRN
Step 4: If amount > 5L, escalate to CFO
Step 5: Post journal entry
""", domain_hint="finance")

# Review and deploy
agent = client.sop.deploy(sop_draft["config"])
print(agent["agent_id"])  # deployed as shadow agent

# Search tenant knowledge and generate/run workflows
matches = client.knowledge.search("vendor renewal policy", top_k=3)
workflow_draft = client.workflows.generate(
    "When a contract renewal is 30 days away, search knowledge, check Jira, "
    "ask contract_intelligence to summarize risk, then notify vendor_manager.",
)
workflow = client.workflows.create(
    name="Renewal Risk Review",
    definition=workflow_draft["workflow"],
    domain="ops",
)
run = client.workflows.run(workflow["id"], payload={"vendor_id": "V-100"})
print(matches[0]["document_name"], run["run_id"])
```

## Quickstart (CLI)

The same `pip install agenticorg` package installs the Python SDK and the
direct `agenticorg` CLI. The CLI is intended for shell-capable assistants and
developer environments such as Claude Code, Codex, Gemini CLI, VS Code
terminals/tasks, CI jobs, and runbooks.

```bash
# Set your API key
export AGENTICORG_API_KEY=your-key

# List agents
agenticorg agents list
agenticorg agents list --domain finance

# Run an agent
agenticorg agents run ap_processor --input '{"invoice_id": "INV-001"}'
agenticorg agents run commerce_sales_agent --action buyer_discovery_preview --input '{"merchant_id":"merchant_demo"}'

# Generate agents, query knowledge, and run workflows
agenticorg agents generate "Create a contract intelligence agent using Confluence and Jira"
agenticorg knowledge search "vendor renewal policy" --top-k 3
agenticorg workflows generate "Review vendor renewal risk using KB and Jira"
agenticorg workflows run wf-123 --input '{"vendor_id":"V-100"}'

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
| `client.agents` | `list()`, `get(id)`, `run(type, inputs=...)`, `create(...)`, `generate(description, deploy=False)` |
| `client.connectors` | `list()`, `get(id)` |
| `client.sop` | `parse_text(text)`, `upload(file)`, `deploy(config)` |
| `client.a2a` | `agent_card()`, `agents()` |
| `client.mcp` | `tools()`, `call(name, args)` |
| `client.workflows` | `templates()`, `list()`, `generate(description)`, `create(...)`, `get(id)`, `run(id, payload=...)`, `get_run(id)` |
| `client.knowledge` | `search(query, top_k=5)` |

## License

Apache-2.0
