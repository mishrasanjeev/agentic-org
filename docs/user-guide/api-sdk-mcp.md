## Choose an integration surface

Use REST/OpenAPI for application contracts, the repository SDKs for client access, MCP for compatible tool clients, and A2A v1 HTTP+JSON for synchronous text between agents after merchant or tenant authorization. Older AgenticOrg A2A-shaped card/task routes are proprietary; they are not the v1 wire contract. The authenticated **A2A / MCP** screen shows integration material. Public discovery does not grant access to business data or actions. See [external A2A buyer access](../a2a-interoperability.md).

| Surface | Suitable first task | Boundary |
| --- | --- | --- |
| REST/OpenAPI | Inspect a resource with an approved credential | Deployed API/schema is the contract |
| Python/TypeScript SDK | Company-scoped read or bounded agent run | Installed client version may differ from repository source |
| MCP stdio adapter | List agents/tools in a compatible local client | Client transport support and scopes are still required |
| A2A v1 HTTP+JSON | Read generic card, obtain scoped credential, send synchronous text | Merchant buyer credential permits non-binding seller Q&A only |
| Commerce bridges | Read merchant artifacts and prepare handoffs | Channel approval and provider execution remain separate |

## Authenticate and preserve context

Use a credential issued for the reviewed deployment and minimum scopes. Keep it in environment/secret storage. Supply authorized company context for execution paths that require it. Never accept a company ID from untrusted model content without server-side authorization.

### Obtain a workspace API key

An authorized administrator opens **Settings > API Keys**. Enter a meaningful
**Key Name**, choose **Expires In**, then select **Generate Key**. A short-lived
key is preferable for a first integration exercise. The secret is displayed
only once; place it in approved secret storage rather than a shared document.
The key table shows its prefix, status, last-used time and creation time. Use
**Revoke** for an unused or compromised key and confirm the client can no longer
authenticate.

The current form does not offer a custom per-key scope editor. A named key is
not inherently a narrowly scoped business grant. Review server-side credential
permissions and use the appropriate delegated-grant path when action-level
authority is required. Human case decisions require the named approver's
authenticated session; an admin key must not substitute for it.

The Python SDK supports an API key or delegated grant. Example of a read-oriented client:

```python
import os
from agenticorg import AgenticOrg

client = AgenticOrg(
    api_key=os.environ["AGENTICORG_API_KEY"],
    base_url=os.environ["AGENTICORG_BASE_URL"],
)
agents = client.agents.list()
```

Verify the installed package methods before copying examples. The repository contains newer client resources than the currently published Python wheel documented in its README; build/publish version and server deployment must be checked separately.

## Connect MCP

Use the repository MCP adapter with a client that supports its stdio transport. Configure `AGENTICORG_BASE_URL` and one accepted authentication value: `AGENTICORG_API_KEY` or `AGENTICORG_GRANTEX_TOKEN`. Use the client's secure configuration mechanism instead of committing a key in JSON.

The adapter's execution/SOP-submission tools require `company_id`. Its `run_agent` path goes through A2A. Scope enforcement for A2A/MCP depends on the deployment's `AGENTICORG_ROUTE_SCOPE_A2A_MCP` setting; administrators must evaluate and configure it, not assume every discovery route is protected identically.

The `seller.*` tools read cached commerce artifacts; they do not authorize payment, orders or holds. Governed-case human decisions have no shortcut through the machine-authenticated MCP adapter.

## Integration acceptance test

1. Pin a reviewed SDK/adapter version and endpoint.
2. Make a bounded permitted read.
3. Test missing/expired credentials and an insufficient scope.
4. Test cross-company access refusal.
5. Inspect error shapes, retry behavior and rate limits.
6. Prove any external action separately with authoritative confirmation.

A generic MCP bridge is not a universal buyer plugin or marketplace approval. Check the current client/provider documentation before enabling a production surface.

An external A2A buyer agent needs a credential issued by the merchant's tenant
admin. This is a bearer capability, not proof that the client belongs to a
named third-party vendor. Revocation, artifact freshness, and tenant/merchant
binding are checked by the server on each request. No purchase is executed by
the synchronous A2A message path.

For a working external client and a temporary synthetic seller catalog, follow
the [local A2A demo](/docs/external-buyer-a2a-demo). The buyer process imports
no AgenticOrg runtime code and exercises discovery, sourced questions, purchase
refusal, and revocation over HTTP.
