# MCP Product Model — Decision Record

Status: **decided 2026-04-18 (PR-A, Enterprise Readiness P3)**

## Context

Two incompatible MCP stories have been told across the product:

1. **Agents-as-tools.** Each AgenticOrg agent is exposed as a single MCP tool named `agenticorg_<agent_type>`. External clients (Claude Desktop, Cursor, ChatGPT MCP, etc.) see one tool per agent and invoke it with a task payload.
2. **Connectors-as-tools.** Raw connector actions (e.g. `slack_send_message`, `jira_create_issue`) are exposed directly as MCP tools.

As of pre-PR-A audit:
- `api/v1/mcp.py` backend implements **agents-as-tools only** (`/mcp/tools` enumerates `agenticorg_<type>`; `/mcp/call` requires the `agenticorg_` prefix).
- `mcp-server/src/index.ts` (the Node MCP-server that external clients connect to) advertises a **hybrid**: `run_agent` as a dedicated tool + `list_mcp_tools` proxying the backend + `call_connector_tool` which purports to invoke connector actions directly but ends up calling `/mcp/call` (which rejects anything without `agenticorg_` prefix).

That mismatch shows up as failed MCP-client calls in the wild and inconsistent marketing copy.

## Decision: agents-as-tools

The supported MCP product model is **agents-as-tools**. Rationale:

1. Matches the existing backend implementation (least code churn).
2. Aligns with the product pitch: AgenticOrg ships virtual employees (agents), not raw tools. An MCP client talking to AgenticOrg should see virtual employees, not the connectors beneath them.
3. One permission boundary (agent scope) instead of two (agent scope + per-connector scope).
4. Reasoning, HITL, and audit all happen at the agent layer — connectors-as-tools would bypass them and defeat governance.

## Naming + discovery

- **Tool name**: `agenticorg_<agent_type>` (e.g. `agenticorg_ap_processor`).
- **Discovery**: `GET /api/v1/mcp/tools` returns `{"tools": [{name, description, inputSchema}]}` where every `name` starts with `agenticorg_`.
- **Invocation**: `POST /api/v1/mcp/call` with `{name, arguments}`; backend strips the `agenticorg_` prefix, validates the agent_type exists, and runs it via the standard agent execution path. Response follows the canonical `AgentRunResult` shape documented in `docs/api/agent-run-contract.md`.

## Unsupported-tool error contract

Clients sending a tool name that isn't in the discovered catalog get an explicit error, not a generic 500:

```json
{
  "error": "unknown_tool",
  "name": "<offending name>",
  "supported_prefix": "agenticorg_",
  "hint": "Call GET /api/v1/mcp/tools for the current catalog"
}
```

Status code: `404`. Implemented in `api/v1/mcp.py`.

## What this removes

- `mcp-server/src/index.ts` no longer advertises `call_connector_tool`. That name promised direct connector invocation the backend never supported.
- Marketing + README copy that says "Expose 340+ tools to ChatGPT/Claude" is revised to "Expose every AgenticOrg agent as an MCP tool". Tool counts come from `/api/v1/product-facts.agent_count`, not a fabricated connector-tool figure.

## Compatibility

- Legacy clients that happened to call `agenticorg_*` tools continue to work without change.
- Legacy clients calling `call_connector_tool` fail loudly (404 with the error shape above). No silent-drop.
- The MCP server exports a `@deprecated` note on any tool that is removed, so IDE auto-complete surfaces the change at build time.

## Tests

- `tests/integration/test_mcp_contract.py` — every discovered tool is invocable; unsupported names return the documented 404 shape.
- `ui/e2e/mcp-integration-page.spec.ts` — the Integrations page copy reflects the chosen model and does not reference removed tools.
