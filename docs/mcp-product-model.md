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

The supported MCP export model is **agents-as-tools**. Rationale:

1. Matches the existing backend implementation (least code churn).
2. Aligns with the product pitch: AgenticOrg ships virtual employees (agents), not raw tools. An MCP client talking to AgenticOrg should see virtual employees, not the connectors beneath them.
3. One permission boundary (agent scope) instead of two (agent scope + per-connector scope).
4. Reasoning, HITL, and audit all happen at the agent layer — connectors-as-tools would bypass them and defeat governance.

## Incoming remote tools are a separate direction

AgenticOrg agents can also consume tenant-owned external MCP tools through
**Connectors > Remote MCP**. This is not the removed `call_connector_tool`
export and does not expose raw customer connectors to external clients.

The incoming path uses public HTTPS Streamable HTTP and customer-supplied bearer
credentials. Discovery, agent selection, save validation and dispatch share a
persisted tenant-scoped catalog with `mcp_connection__tool` identities. Credentials
remain encrypted; runtime checks ownership, agent links, grant scope and schema
freshness. Tools default to write and remain contained unless reviewed as reads.
This does not provide a general remote-write approval/execution workflow.

See the [remote connector setup guide](user-guide/connectors.md#connect-a-remote-mcp-server)
for supported schemas, limits and refusal handling. OAuth, local stdio and legacy
SSE endpoints are not supported by this incoming path. Protocol-level local tests
do not establish live speech, messaging or payment-provider functionality.

## Naming + discovery (exported AgenticOrg agents)

- **Tool name**: `agenticorg_<agent_type>` (e.g. `agenticorg_ap_processor`).
- **Discovery**: `GET /api/v1/mcp/tools` returns `{"tools": [{name, description, inputSchema}]}` where every `name` starts with `agenticorg_`.
- **Invocation**: `POST /api/v1/mcp/call` with `{name, arguments}`; backend strips the `agenticorg_` prefix, validates the agent_type exists, and runs it via the standard agent execution path. Response follows the canonical `AgentRunResult` shape documented in `docs/api/agent-run-contract.md`.
- **Scope**: when the deployment sets `AGENTICORG_ROUTE_SCOPE_A2A_MCP=true`, `POST /api/v1/mcp/call` needs `mcp:write` (the `mcp:call` in default API key scopes is accepted as its alias) or `agenticorg:admin`; with the setting off (the default) any authenticated credential may call it. Discovery (`GET /api/v1/mcp/tools`) is public either way. See `docs/operations/grant-enforcement.md`.

Governed-case roles (`business_underwriter`, `screening_disposition`) are
separate case-runtime identities, not entries in the general MCP agent catalog.
Do not route a case investigation or a human case decision through `mcp/call`.
The case API checks its own tenant flag, local exact-purpose allowlist and
delegated grant for every provider call. Case decisions and reviews require a
signed-in human; an MCP token is not a human session.

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
