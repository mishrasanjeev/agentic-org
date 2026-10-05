## Connector readiness is more than a green badge

A connector links the agent runtime to a business service. It needs the right tenant/company binding, credentials, provider permissions, tool implementation and operational health. A registered connector or successful health check does not prove that every advertised business action works.

![Illustrative connector catalog](/screenshots/connectors.webp)

This existing product screenshot illustrates the integration catalog, not live readiness in your tenant.

## Prepare the connection

Name the integration owner and system-of-record owner. Obtain an approved sandbox or read-only account. List the exact data and actions needed. For BFSI, a core banking, LOS, claims or screening service requires the institution's approved API contract and access process; selecting a generic connector does not create that integration.

## Register and bind

1. Open **Connectors**, find the provider in the native catalog, and select **Register**. Use the general **Register Connector** button only for a reviewed custom integration.
2. When native prefill is enabled, confirm the fixed provider name matches the registry entry. Otherwise enter the canonical registry name manually; a display label is not enough.
3. Enter the connector name, reviewed base URL, category, authentication type and rate limit.
4. Use the provider-specific protected credential fields or approved secret reference. Do not put secrets in prompts, screenshots or ordinary task inputs.
5. Review extra JSON configuration against the provider's contract. Do not assume arbitrary configuration creates a tool implementation.
6. Bind the connector to the correct company or documented tenant-wide scope.
7. Run the supported health/test action and inspect the safe result.

The native catalog's **Register** button can prefill the connector's exact registry name, category, endpoint and authentication type when the deployment enables the native-prefill rollout. Check the name before registering; a display label is not the registry ID. An unknown or outdated catalog link is refused rather than creating a generic connector. Without that rollout, the form remains the existing manual registration flow. For the four commonly requested providers, the reviewed credential keys are `access_token` and `phone_number_id` (WhatsApp), `account_sid` and `auth_token` (Twilio), `client_id`, `client_secret` and `refresh_token` (Gmail), and `client_id`, `client_secret` and `merchant_id` (Plural sandbox). A connector may also need provider-side permissions or additional settings. Do not paste real credentials into an issue or test report.

Registration stores credentials in the encrypted connector vault; it does not prove that the upstream account is authorized. A green connection probe proves only the operation it exercised. Custom or external MCP servers are not native connector classes on the current main runtime; listing tools in another deployment is not proof that this deployment can authorize or execute them. Do not treat an unrecognized custom connector as callable merely because registration returned an ID.

Customers can bring credentials for their own provider accounts. WhatsApp needs a permitted business token and phone-number ID; Twilio needs an account SID and auth token; the guided Gmail form needs a customer OAuth client plus refresh token; Plural needs merchant-issued sandbox credentials before any live-rail review. The Gmail connection test now reads the authenticated user's profile without returning the mailbox address, and a successful profile check does not authorize sending mail. Register first, run the provider's bounded connection test, then separately prove each intended tool and scope with that customer's approved sandbox account. Leave the connector unverified if consent, scope, or provider access is missing.

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
