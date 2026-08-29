# AgenticOrg Python SDK

> AgenticOrg is owned by **Orchestrum Technologies LLP**. Inventor / Owner:
> **Sanjeev Kumar**. Contact [sanjeev@orchestrum.in](mailto:sanjeev@orchestrum.in)
> or [mishra.sanjeev@gmail.com](mailto:mishra.sanjeev@gmail.com). See
> [Ownership and contact](../docs/OWNERSHIP.md).

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
| `client.knowledge` | search, supported types, upload, documents, delete, health, stats | Uploads can invoke OCR and indexing; inspect returned status. |
| `client.voice` | status, config, provider test, runtime health, calls, outbound call | Outbound calls are explicit and can incur provider charges. |
| `client.rpa` | scripts, history, run | A selected script can perform external browser actions. |
| `client.bridges` | register, list, status, route, deregister | Routes only to an authorized registered local bridge. |
| `client.commerce` | seller onboarding, Shopify sync, artifact/cache, buyer ask, adapters, mandate evidence, purchase/POS preparation | Purchase and POS helpers prepare handoffs; provider/POS systems remain transaction authorities. |

## Runtime examples

```python
types = client.knowledge.supported_types()
document = client.knowledge.upload("scanned-invoice.pdf")

products = client.commerce.products(merchant_id="merchant-123")
answer = client.commerce.ask({
    "merchant_id": "merchant-123",
    "question": "Which variants are fresh and in stock?",
})

voice = client.voice.status()
# Runtime health and call history are agent-scoped.
runtime = client.voice.runtime_health("00000000-0000-0000-0000-000000000001")
rpa_catalog = client.rpa.scripts()
```

`place_outbound_call`, `rpa.run`, `bridges.route`, Shopify sync, and provider
verification are intentionally explicit methods. Call them only with reviewed
tenant authorization and real external-action intent.

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
