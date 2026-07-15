# AgenticOrg Python SDK

Repository client for selected AgenticOrg HTTP APIs. Method availability and
response shape depend on the installed SDK version, configured endpoint,
authentication, tenant/company access, grants, and backend deployment. Examples
below are integration candidates, not evidence of production availability or a
successful provider action.

## Install

When the package is available from the configured Python package registry:

```bash
pip install agenticorg
```

Pin and review the package version used by your application.

## Authentication

Create a client with an API key or delegated grant accepted by the configured
endpoint:

```python
from agenticorg import AgenticOrg

client = AgenticOrg(
    api_key="your-key",
    base_url="https://your-reviewed-endpoint.example",
)
```

Credentials identify a caller; they do not replace tenant/company authorization
or tool grants. Keep secrets outside source control.

## Company-scoped shadow candidate

Use an explicit company identifier when creating or generating a candidate.
Keep the initial state in shadow until separately reviewed and promoted:

```python
company_id = "00000000-0000-0000-0000-000000000001"

candidate = client.agents.create(
    company_id=company_id,
    name="Invoice review candidate",
    agent_type="invoice_review_candidate",
    domain="finance",
    authorized_tools=[],
    initial_status="shadow",
)

result = client.agents.run(
    candidate["agent_id"],
    action="review_draft",
    inputs={"document_ref": "sample-document"},
    context={"company_id": company_id},
)

print(result.status)
print(result.output)
```

The direct run uses the stored agent record; the backend must still verify its
tenant and company ownership. Do not use a company identifier supplied by
untrusted content without an authorized mapping.

## SOP draft workflow

SOP parsing produces a draft, not an approved or launchable agent:

```python
draft = client.sop.parse_text(
    "Receive a document, validate required fields, and route exceptions for review.",
    domain_hint="finance",
)

reviewed_config = dict(draft["config"])
reviewed_config["company_id"] = company_id
reviewed_config["initial_status"] = "shadow"

candidate = client.sop.deploy(reviewed_config)
print(candidate["initial_status"])
```

Before submission, review instructions, data sources, tools, grants, evidence,
approval rules, and the target company. Submission does not authorize promotion
or external actions.

## Discovery resources

| Resource | Selected methods | Boundary |
|---|---|---|
| `client.agents` | `list`, `get`, `run`, `create`, `generate` | Returned records and actions are endpoint-specific. |
| `client.connectors` | `list`, `get` | Discovery does not prove provider configuration. |
| `client.sop` | `parse_text`, `upload`, `deploy` | Parsed output requires review; deploy creates a candidate per backend policy. |
| `client.a2a` | `agent_card`, `agents` | Public discovery data can differ from authenticated runtime access. |
| `client.mcp` | `tools`, `call` | Tool records do not create execution authority. |
| `client.workflows` | generation, CRUD, run methods | Availability depends on the configured backend. |
| `client.knowledge` | `search` | Results depend on authorized indexed content. |

Inspect actual responses and errors instead of relying on illustrative output.
Authorization denials should remain denials; do not automatically broaden
credentials, scopes, or company context.

## CLI

If the installed distribution provides the CLI, inspect commands with:

```bash
agenticorg --help
```

CLI behavior follows the same endpoint, evidence, authentication, and company
boundaries as the Python client.

## License

Apache-2.0
