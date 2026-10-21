# agenticorg-mcp-server

> AgenticOrg is owned by **Orchestrum Technologies LLP**. Inventor / Owner:
> **Sanjeev Kumar**. Contact [sanjeev@orchestrum.in](mailto:sanjeev@orchestrum.in)
> or [mishra.sanjeev@gmail.com](mailto:mishra.sanjeev@gmail.com). See
> [Ownership and contact](../docs/OWNERSHIP.md).

Repository MCP adapter for AgenticOrg API surfaces. The server exposes records
and actions returned by a configured AgenticOrg endpoint; tool presence is not
evidence that an agent, connector, provider, or external action is available in
a particular deployment.

Compatibility is conditional on an MCP client that supports the configured
stdio transport and the package's protocol-library version. Authentication,
tenant access, company access, grants, backend routes, and provider setup must
also be valid at runtime.

## Install and verify

Pin the reviewed release after confirming it exists in your npm registry:

```bash
npm install -g agenticorg-mcp-server@5.1.0
AGENTICORG_API_KEY=your-key agenticorg-mcp
```

### Upgrade from 4.x

Version 5 requires `company_id` for `run_agent` and `deploy_agent`. Update
saved tool arguments with a company that the authenticated tenant can access;
never substitute a tenant ID. SOP submission produces a shadow candidate and
does not bypass review or promotion. The new seller tools read cached facts
only and do not authorize purchases or payments.

An npm release does not update the MCP registry or an installed client.
Confirm both stores and restart the client after upgrading:

```bash
npm view agenticorg-mcp-server@5.1.0 version
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
      "args": ["--yes", "agenticorg-mcp-server@5.1.0"],
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

A delegated grant is checked against each API route's scope exactly as an API
key is. `list_agents` and `get_agent_details` need `agents:read` in the grant,
and `list_connectors` needs `connectors.read`; a grant with only tool scopes
gets `403` from them. `run_agent` goes through A2A (`POST /api/v1/a2a/tasks`),
which is not scope-checked unless the deployment sets
`AGENTICORG_ROUTE_SCOPE_A2A_MCP=true`; then it needs `a2a:write`.

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
Set `AGENTICORG_MCP_COMMERCE_ONLY=true` to expose only those seller tools (and
the UCP tools below when configured); this setting limits the visible tool
surface but does not create transaction authority.

### UCP checkout tools

When `AGENTICORG_UCP_PROFILE_URL` names a merchant's UCP business profile
(`https://<merchant serving host>/.well-known/ucp`), the server reads that
profile at startup and registers the UCP 2026-04-08 MCP binding tools for the
capabilities it advertises. The profile lists checkout, cart and order only
while the API runs with `AGENTICORG_UCP_CHECKOUT_ENABLED=true`; otherwise, or
when the profile cannot be read, none of these tools is registered. With both
UCP releases enabled the server follows the profile's 2026-04-08
`supported_versions` leaf. Calls go to the profile's `dev.ucp.shopping` REST
endpoint, which must be on the same origin as the profile (https only), with
the configured credential (a tenant API key holding `ucp:read`/`ucp:write`).

| Tool | Capability | Annotations | Behavior |
|---|---|---|---|
| `create_checkout` | checkout | not read-only, not destructive, not idempotent | Creates a session; nothing is charged. |
| `get_checkout` | checkout | read-only, idempotent | Reads a session. |
| `update_checkout` | checkout | destructive, idempotent | Full replacement of the session contents. |
| `complete_checkout` | checkout | destructive, not idempotent | Two steps, see below. |
| `cancel_checkout` | checkout | destructive, idempotent | Terminal cancel. |
| `create_cart` / `get_cart` / `update_cart` / `cancel_cart` | cart | as for checkout | Cart operations. |
| `get_order` | order | read-only, idempotent | Reads an order created by a completed checkout. |

All UCP tools carry `openWorldHint: true`. Arguments follow the binding:
`meta` (`ucp-agent.profile` and `idempotency-key`), `id`, and the `checkout` or
`cart` object. `meta.ucp-agent.profile` defaults to
`AGENTICORG_UCP_AGENT_PROFILE`; a call with neither is refused before any
request. `complete_checkout`, `cancel_checkout` and `cancel_cart` require
`meta.idempotency-key` (a UUID), as the binding does; other writes generate one
when it is absent.

`complete_checkout` never sends a Complete that could execute without a sealed
allow decision:

1. Call it with `decide_only: true`. The merchant's commerce arbiter decides
   `session_complete` for the exact session and payment; nothing executes. The
   result carries `ai.agenticorg.commerce.decision` with `decision_id`,
   `outcome` and `expires_at`.
2. Call it again with that `decision_id` (and the same payment). The API
   consumes the decision once, bound to the session payload hash; a changed
   session or credential is refused.

Both steps need a payment executor registered in the API for the merchant: the
Plural executor, which the API registers only with
`PLURAL_PINE_LIVE_EXECUTION_ENABLED=true` (off by default). Without it Complete
answers `executor_unavailable` (a business outcome, below) and the session
stays `ready_for_complete`.

A call with neither, or with both, is refused by this server without contacting
the API. `decide_only` and `decision_id` may also be sent inside the checkout's
`ai.agenticorg.commerce` member (with `buyer_authority`); conflicting values are
refused. Business outcomes (an out-of-stock item, an arbiter refusal,
`executor_unavailable`) are tool results with UCP `messages[]`, returned in
`structuredContent` and as text. HTTP errors become MCP tool errors carrying only
`{status, code, content}` from the UCP error body; nothing else from the response is
returned. The decision in a result keeps only `decision_id`, `action`, `outcome`,
`failing_rule`, `payload_hash` and `expires_at`.

These tools are a local stdio adapter: no MCP transport is advertised in the
merchant's UCP profile. Catalog tools (`search_catalog`, `lookup_catalog`,
`get_product`) are not exposed.

Direct connector invocation is not exposed by this server. Connector records
and agent-tool records are discovery data, not proof that provider credentials,
permissions, or live accounts are configured.

Governed business cases use separate `/api/v1/governed-cases` routes. The
`business_underwriter` and `screening_disposition` case roles are not MCP
agent tools, and `list_mcp_tools` must not be interpreted as a case-execution
or human-approval catalog. Creating a case or scheduling an investigation via
the repository SDKs requires tenant enablement and server-side grant checks.
Review, withdrawal, information-request approval and case decisions require a
signed-in human; this MCP adapter accepts machine credentials and provides no
shortcut for those operations. See the [case lifecycle](../docs/governance/case-lifecycle.md).

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

Apache License 2.0 (SPDX `Apache-2.0`); see [LICENSE](LICENSE). Copyright 2026 Orchestrum
Technologies LLP.

**This MCP server is open source and free for any use, including commercial use**, under the Apache
License 2.0. It stays Apache-2.0 after the AgenticOrg platform moved to the PolyForm Noncommercial
License 1.0.0 on 21 October 2026. The platform itself (the API, workers, UI and the rest of the
repository) is licensed separately: commercial use of the platform needs a written agreement with
Orchestrum Technologies LLP; see
[COMMERCIAL-LICENSE.md](https://github.com/mishrasanjeev/agentic-org/blob/main/COMMERCIAL-LICENSE.md).
"AgenticOrg" and "Grantex" are trademarks of Orchestrum Technologies LLP; the Apache License 2.0
grants no trademark rights
([TRADEMARKS.md](https://github.com/mishrasanjeev/agentic-org/blob/main/TRADEMARKS.md)).
