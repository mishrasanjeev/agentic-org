# Scenario 3 — Developer integration (SDK + MCP)

**Persona:** platform engineer integrating AgenticOrg into an existing
product or an MCP-capable client (Claude Desktop, Cursor, ChatGPT)
**Goal:** run an agent from Python/TS code and from an MCP client,
and rely on the same canonical contract across both paths
**Playwright:** `ui/e2e/sdk-examples.spec.ts`
**Regression:** `tests/regression/test_agent_run_contract.py`

## Steps

### Python SDK

```python
from agenticorg import AgenticOrg

client = AgenticOrg(api_key="ao_sk_...")
result = client.agents.run(
    agent_type="cfo_kpi_monitor",
    inputs={"company_id": "demo-corp"},
)

# Canonical shape (see sdk/agenticorg/client.py::AgentRunResult)
assert result.run_id
assert result.status in ("succeeded", "failed", "hitl_pending")
assert result.output is not None
assert 0.0 <= result.confidence <= 1.0
assert result.reasoning_trace
assert result.tool_calls
```

### TypeScript SDK

```ts
import { AgenticOrg } from "agenticorg-sdk";

const client = new AgenticOrg({ apiKey: "ao_sk_..." });
const result = await client.agents.run({
  agentType: "cfo_kpi_monitor",
  inputs: { company_id: "demo-corp" },
});

// Same canonical shape — see sdk-ts/src/index.ts::AgentRunResult
```

### MCP client (Claude Desktop / Cursor / ChatGPT)

Register AgenticOrg as an MCP server (`npx agenticorg-mcp-server` with
the same `AGENTICORG_API_KEY`). The client sees every agent as a tool
named `agenticorg_<agent_type>`. Invoking a tool returns the same
`AgentRunResult` structure, surfaced as the tool response.

Unknown tool names return HTTP 404 with a structured body
`{error, name, supported_prefix, hint}` — not a generic 400 — so the
MCP client surfaces a useful error.

## Expected outcome

- SDK examples compile and execute against the live API with no
  schema mismatch.
- Python + TS + MCP all return the same canonical field set.
- Legacy aliases (`task_id`, `result.output`, `result.confidence`) are
  accepted as inputs but deprecated in returns — through v5.0.

## Drift guards

- `tests/regression/test_agent_run_contract.py` — every canonical
  field is asserted on the Python SDK's round-trip, the direct API
  response, and the MCP tool response.
- `ui/e2e/sdk-examples.spec.ts` — snippets on the Settings page render
  and are copy-pasteable.
