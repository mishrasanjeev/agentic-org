## Connector readiness is more than a green badge

A connector links the agent runtime to a business service. It needs the right tenant/company binding, credentials, provider permissions, tool implementation and operational health. A registered connector or successful health check does not prove that every advertised business action works.

![Illustrative connector catalog](/screenshots/connectors.webp)

This existing product screenshot illustrates the integration catalog, not live readiness in your tenant.

## Prepare the connection

Name the integration owner and system-of-record owner. Obtain an approved sandbox or read-only account. List the exact data and actions needed. For BFSI, a core banking, LOS, claims or screening service requires the institution's approved API contract and access process; selecting a generic connector does not create that integration.

## Register and bind

1. Open **Connectors** and the **Register Connector** screen.
2. Select the available provider or **Custom / Generic Connector**.
3. Enter the connector name, reviewed base URL, category, authentication type and rate limit.
4. Use the provider-specific protected credential fields or approved secret reference. Do not put secrets in prompts, screenshots or ordinary task inputs.
5. Review extra JSON configuration against the provider's contract. Do not assume arbitrary configuration creates a tool implementation.
6. Bind the connector to the correct company or documented tenant-wide scope.
7. Run the supported health/test action and inspect the safe result.

The exact credential form differs by provider. Protected storage is not permission to grant unlimited upstream scopes. Prefer read-only scopes for the first pilot.

## Grant the agent only what it needs

Open the agent configuration and select the appropriate connector and **Authorized Tools**. Verify that a permitted read call works for the selected company. Then verify that an ungranted tool and another company's resource are refused. Model access and connector access must be tested independently.

## Validate the real contract

| Check | Evidence to collect |
| --- | --- |
| Authentication | A successful bounded provider request, without logging its secret |
| Scope | Required read/action allowed; forbidden action rejected |
| Mapping | Identifiers, dates, amounts, currencies and pagination are correct |
| Freshness | Timestamp/source state reaches the user-facing output |
| Failure | Rate limit, expired credential and unavailable provider are visible |
| Recovery | Safe retry/idempotency and an accountable escalation path |

Health freshness and credential presence are readiness signals, not an error-budget or sustained-sync guarantee. Run representative volume tests before relying on a connection operationally.

## Maintain and troubleshoot

Rotate credentials through the approved secret path. Re-test after provider API, schema, scopes or tenant-binding changes. Monitor latency, failures and rate limits. Disable dependent schedules when the source contract changes.

If company A works and company B does not, inspect binding before changing the code or copying IDs. If a custom URL is rejected by network policy, have security approve the legitimate endpoint; do not turn off SSRF or private-network protections.

Next: [Workflows](/docs/workflows), [Model setup](/docs/model-setup), [BFSI reconciliation](/docs/bfsi-reconciliation).
