# AgenticOrg Read-Only Production Commerce Discovery Dependency

Status: planning proposal only  
Date: 2026-05-18  
Scope: AgenticOrg dependency on future Grantex read-only production Commerce discovery  
Production changes made by this proposal: none  
AgenticOrg commerce public discovery enabled by this proposal: no  
Grantex production Commerce V1 enabled by this proposal: no  
Checkout or payment creation enabled by this proposal: no  
Live payments enabled by this proposal: no  
Live Plural enabled by this proposal: no  
Secrets inspected or changed: no

This document records the AgenticOrg side of the future Grantex read-only
production discovery decision. It is not an approval to expose AgenticOrg
commerce discovery metadata in production.

## Current Production Posture

C5A deployed the AgenticOrg public commerce discovery gate successfully:

- Public commerce discovery is hidden by default.
- `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED` is absent or not true in
  production.
- Public MCP tools do not expose `agenticorg_commerce_sales_agent`.
- Public A2A discovery does not expose `commerce_sales_agent`.
- Public discovery does not expose `grantex_commerce:*` tool metadata.
- Non-commerce discovery remains available.
- Grantex production Commerce V1 discovery remains disabled and fail-closed.
- Live payments and live Plural remain disabled.

The existing AgenticOrg gate name is:

- `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED`

Default behavior:

- absent: disabled
- empty: disabled
- invalid: disabled
- anything except an explicit safe true value: disabled

The safe true values are implementation-defined in the AgenticOrg discovery gate
code and are intended for local/test or explicitly approved environments only.

## Dependency On Grantex

AgenticOrg commerce discovery metadata should not become public before Grantex
has an approved read-only production Commerce discovery posture.

The reason is dependency ordering:

1. AgenticOrg commerce tools are Grantex-only.
2. AgenticOrg does not call Stripe, Plural, Pine, or provider APIs directly for
   commerce.
3. Public AgenticOrg commerce metadata points users and agents toward Grantex
   commerce capabilities.
4. If Grantex production Commerce discovery remains disabled, public AgenticOrg
   commerce metadata can overstate the production discovery posture even if the
   execution path remains guarded.

Therefore, AgenticOrg should remain hidden until a future human-approved Grantex
read-only discovery enablement has passed read-only smoke and payload review.

## Proposed Enablement Sequence

Recommended future sequence:

1. Keep AgenticOrg `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED` disabled.
2. Implement a narrow Grantex read-only discovery gate such as
   `COMMERCE_PUBLIC_DISCOVERY_ENABLED`.
3. Keep Grantex broad runtime gate names, checkout/payment creation, live
   payments, and live Plural disabled unless separately approved.
4. Run Grantex read-only production discovery smoke:
   - health
   - JWKS
   - well-known discovery profile
   - secret scan
   - overclaim scan
   - rollback rehearsal or documented rollback command plan
5. Confirm the Grantex discovery profile is non-secret, merchant-approved, and
   wording-approved.
6. Only after explicit human approval, enable AgenticOrg public commerce
   discovery with `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED`.
7. Run AgenticOrg read-only public discovery smoke.
8. Keep checkout/payment/live provider paths disabled unless separately
   approved.

## AgenticOrg Public Discovery Smoke Checklist

Future smoke must use read-only GET requests only.

Before enabling AgenticOrg public commerce discovery:

- Confirm Grantex read-only discovery is enabled through its narrow discovery
  gate, not through broad runtime enablement alone.
- Confirm Grantex discovery smoke passed.
- Confirm no provider credentials or live provider behavior appear in Grantex
  discovery.
- Confirm live payments and live Plural remain disabled.
- Confirm AgenticOrg production config change is limited to the discovery gate.

After enabling AgenticOrg public commerce discovery:

| Check | Expected result |
| --- | --- |
| `GET /api/v1/health` | 200 |
| `GET /api/v1/mcp/tools` | Includes `agenticorg_commerce_sales_agent` only after explicit approval |
| `GET /api/v1/a2a/.well-known/agent.json` | Includes commerce metadata only after explicit approval |
| `GET /api/v1/a2a/agents` | Includes `commerce_sales_agent` only after explicit approval |
| Public metadata | Grantex-only commerce boundary is clear |
| Provider scan | No Stripe, Plural, Pine, provider credential, secret, or raw payload path |
| Wording scan | No live-payment, live-Plural, external-pilot, production-ready, or certification overclaims |

## No-Go Conditions

Do not enable AgenticOrg public commerce discovery if any of these are true:

- Grantex production read-only discovery is still disabled.
- Grantex discovery was enabled only through the broad `COMMERCE_V1_ENABLED`
  runtime gate without a reviewed read-only discovery control.
- Grantex read-only discovery smoke has not passed.
- Grantex discovery payload has not been reviewed for secrets and overclaims.
- Live payments or live Plural would be enabled or implied.
- AgenticOrg would advertise direct Stripe, Plural, Pine, or provider
  credential handling.
- AgenticOrg public metadata would imply production readiness, external pilot
  readiness, AP2/UCP/ACP certification, Plural certification, or provider
  certification.
- There is no rollback owner or rollback checklist.
- Legal/compliance, security, product wording, operations, and named merchant
  approvals are incomplete.

## Decision Table

| Option | Description | Benefits | Risks | Recommendation |
| --- | --- | --- | --- | --- |
| Keep hidden | Leave `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED` disabled. | Matches current safe production posture. | Commerce agent is not publicly discoverable. | Recommended until Grantex read-only discovery is approved and smoke-tested. |
| Gated internal or allowlisted exposure | Enable only in local/test or approved internal contexts. | Allows controlled validation without public metadata. | Requires strict config discipline. | Acceptable for non-production validation. |
| Public Grantex-only commerce discovery | Enable AgenticOrg public metadata after Grantex read-only discovery passes. | Completes public discovery chain. | Public metadata can overstate readiness if wording drifts. | Consider only after explicit human approval. |

## Rollback Checklist

Rollback must not require secret rotation.

1. Disable the AgenticOrg discovery gate:
   - `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED`
2. Redeploy or revise using the normal approved production process if required
   by the platform.
3. Confirm read-only endpoints hide commerce metadata:
   - `/api/v1/mcp/tools`
   - `/api/v1/a2a/.well-known/agent.json`
   - `/api/v1/a2a/agents`
4. Confirm non-commerce discovery and health remain available.
5. Confirm Grantex discovery posture is unchanged or disabled as intended.
6. Confirm live payments and live Plural remain disabled.
7. Record rollback evidence without raw payloads or secret values.

## Recommendation

Keep AgenticOrg public commerce discovery hidden until Grantex read-only
production Commerce discovery has an independent gate, merchant-scoped payload
approval, read-only smoke evidence, and explicit human launch approval.

Do not use AgenticOrg public discovery to get ahead of Grantex discovery. The
Commerce Sales Agent remains implemented and tested, but production discovery
metadata should stay hidden until the upstream Grantex production discovery
posture is approved.

## Future Implementation Prompt

Do not run this prompt until human-approved.

```text
Task: C5C implementation only - enable AgenticOrg public commerce discovery after approved Grantex read-only discovery.

Do not deploy unless explicitly approved.
Do not merge unless explicitly approved.
Do not create cloud resources.
Do not enable checkout/payment creation.
Do not enable live payments.
Do not enable live Plural.
Do not touch secrets.
Do not run state-changing production requests.

Preconditions:
1. Grantex read-only production Commerce discovery is enabled through a narrow
   discovery gate such as COMMERCE_PUBLIC_DISCOVERY_ENABLED.
2. Grantex read-only smoke passed.
3. Grantex discovery payload passed secret and overclaim scans.
4. Legal/compliance, security, product wording, operations, and named merchant
   approvals are complete.

Goal:
Prepare the AgenticOrg change plan to expose existing Grantex-only commerce
metadata through public MCP/A2A discovery by setting
AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED in an approved production deploy.

Checks:
1. Confirm mock mode remains default.
2. Confirm no direct Stripe/Plural/Pine/provider credential path exists.
3. Confirm public metadata is Grantex-only.
4. Confirm live payment and live Plural language is absent.
5. Confirm rollback hides commerce metadata by disabling
   AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED.

Validation:
- Read-only GET smoke for health, MCP tools, A2A agent card, and A2A agents.
- Secret scan and overclaim scan on summarized evidence.
- No state-changing production requests.
```
