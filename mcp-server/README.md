# agenticorg-mcp-server

Repository MCP adapter for AgenticOrg API surfaces. The server exposes records
and actions returned by a configured AgenticOrg endpoint; tool presence is not
evidence that an agent, connector, provider, or external action is available in
a particular deployment.

Compatibility is conditional on an MCP client that supports the configured
stdio transport and the package's protocol-library version. Authentication,
tenant access, company access, grants, backend routes, and provider setup must
also be valid at runtime.

## Install and verify

When the package is available from the configured npm registry:

```bash
npm install -g agenticorg-mcp-server
AGENTICORG_API_KEY=your-key agenticorg-mcp
```

For repository verification:

```bash
cd mcp-server
npm ci
npm test
```

The smoke test validates this repository build against a local stub API. It is
not production, provider, or client-compatibility evidence.

## Client configuration

The exact configuration path and schema belong to the MCP client. A typical
stdio entry is:

```json
{
  "mcpServers": {
    "agenticorg": {
      "command": "npx",
      "args": ["agenticorg-mcp-server"],
      "env": {
        "AGENTICORG_API_KEY": "your-api-key",
        "AGENTICORG_BASE_URL": "https://your-reviewed-endpoint.example"
      }
    }
  }
}
```

Confirm current client documentation before relying on this example.

## Authentication and company context

Set one authentication value:

- `AGENTICORG_API_KEY` for an API key accepted by the configured endpoint; or
- `AGENTICORG_GRANTEX_TOKEN` for a delegated grant accepted by that endpoint.

`AGENTICORG_BASE_URL` selects the API endpoint. Endpoint trust and readiness
require separate verification.

Execution and SOP submission tools require `company_id`. The server sends that
identifier as top-level request data and, for A2A execution, in the task
context. The backend remains responsible for verifying that the authenticated
tenant may access the company. A caller must not substitute a tenant identifier
or infer a company from untrusted content.

## Tool boundaries

| Tool | Repository behavior |
|---|---|
| `list_agents` | Lists agent records returned by the configured endpoint. |
| `run_agent` | Submits a company-scoped request to the A2A task endpoint. |
| `get_agent_details` | Reads one agent record. |
| `create_agent_from_sop` | Parses text into a draft configuration for review. |
| `deploy_agent` | Submits a reviewed configuration as a company-scoped shadow candidate. |
| `list_connectors` | Lists connector records for discovery only. |
| `list_mcp_tools` | Lists environment-specific agent-tool records. |
| `discover_agents_a2a` | Reads A2A skill records. |
| `get_agent_card` | Reads the configured endpoint's public discovery document. |

The `seller.*` tools are read-oriented views over cached commerce artifacts.
Their output can be stale or incomplete and does not authorize checkout,
payment, order, hold, refund, return, shipping, reservation, or mandate action.
Set `AGENTICORG_MCP_COMMERCE_ONLY=true` to expose only those seller tools; this
setting limits the visible tool surface but does not create transaction authority.

Direct connector invocation is not exposed by this server. Connector records
and agent-tool records are discovery data, not proof that provider credentials,
permissions, or live accounts are configured.

## Company-scoped execution example

```json
{
  "agent_type": "commerce_sales_agent",
  "company_id": "00000000-0000-0000-0000-000000000001",
  "action": "buyer_discovery_preview",
  "inputs": {
    "merchant_id": "merchant_demo",
    "request_text": "show a read-only discovery preview"
  }
}
```

Identifiers and responses in this example are illustrative. Review the returned
status and evidence; do not treat a response as approval for an external action.

## SOP workflow

`create_agent_from_sop` returns a draft. A reviewer must check the proposed
instructions, tools, grants, policy thresholds, evidence, and company. If the
reviewer chooses to submit it, `deploy_agent` requires the reviewed `config` and
the target `company_id`. The repository backend creates a shadow candidate;
further promotion requires the backend's separate authorization path.

## Troubleshooting

- Confirm the client supports stdio MCP servers and inspect its own MCP logs.
- Confirm the selected endpoint, credentials, tenant, and company are correct.
- Treat an HTTP authorization or scope error as a denial; do not retry with a
  broader identity without explicit authorization.
- Use `list_mcp_tools` and connector listing only as environment-specific
  discovery, not as an availability promise.

## License

Apache-2.0
