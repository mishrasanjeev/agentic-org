# AgenticOrg Read-Only Commerce Discovery Rollout Dependency

Status: planning package only
Date: 2026-05-18
Scope: AgenticOrg dependency on a future Grantex read-only production Commerce discovery rollout
Production changes made by this package: none
AgenticOrg commerce public discovery enabled by this package: no
Grantex production Commerce V1 enabled by this package: no
Checkout or payment creation enabled by this package: no
Live payments enabled by this package: no
Live Plural enabled by this package: no
Secrets inspected or changed: no

This document defines the AgenticOrg dependency posture for a later
human-approved Grantex read-only production Commerce discovery rollout. It is a
planning package only and does not approve exposing AgenticOrg commerce
metadata.

## Current Dependency Status

C5A deployed the AgenticOrg public commerce discovery gate and confirmed the
production default is hidden. The gate name is:

- `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED`

Current required posture:

- AgenticOrg public MCP discovery must not expose
  `agenticorg_commerce_sales_agent`.
- AgenticOrg public A2A discovery must not expose `commerce_sales_agent`.
- AgenticOrg public discovery must not expose `grantex_commerce:*` metadata.
- Non-commerce discovery and health can remain available.
- AgenticOrg commerce execution remains Grantex-only.
- AgenticOrg must not expose direct Stripe, Plural, Pine, or provider
  credential paths for commerce.

Grantex C5C deployed a narrow read-only discovery gate, but production Grantex
Commerce discovery remains fail-closed until a later human-approved rollout.

## Why AgenticOrg Must Stay Hidden

AgenticOrg commerce discovery depends on Grantex discovery because the Commerce
Sales Agent advertises Grantex-controlled tools and policy boundaries.

If AgenticOrg exposes commerce metadata before Grantex read-only production
discovery is approved and smoke-tested, users or agents could infer that a
complete public production discovery chain exists. That would overstate the
current posture.

Therefore:

1. Keep `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED` disabled before Grantex
   read-only discovery rollout.
2. Keep it disabled during the Grantex read-only rollout.
3. Keep it disabled after the Grantex rollout unless a separate AgenticOrg
   approval explicitly enables public commerce discovery.

## Grantex Preconditions Before AgenticOrg Changes

AgenticOrg public commerce discovery may be considered only after all Grantex
conditions below are true:

- Grantex read-only discovery was enabled through the narrow discovery gate,
  not the broad Commerce runtime gate.
- Grantex named merchant approval is complete.
- Grantex read-only smoke passed.
- Grantex discovery payload passed secret and overclaim scans.
- Grantex live payment and live Plural flags remain disabled or absent.
- Grantex checkout/payment creation and MCP runtime remain disabled unless a
  separate runtime approval exists.
- Grantex rollback plan is tested or documented with a named owner.

## AgenticOrg Config Name

The relevant AgenticOrg config name is:

| Config name | Required posture |
| --- | --- |
| `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED` | Remains disabled until a separate AgenticOrg approval after Grantex smoke passes. |

This planning package records the config name only. It does not record or
propose a config value.

## Human Approval Checklist

Before any AgenticOrg public commerce discovery rollout:

| Gate | Required approval |
| --- | --- |
| Grantex dependency | Grantex read-only production discovery is approved, enabled, and smoke-tested. |
| Security | Public MCP/A2A metadata, route gating, rollback, and no-provider-call boundary reviewed. |
| Legal/compliance | Public commerce wording, consent/payment language, and merchant references reviewed. |
| Product wording | Capability language approved and confirmed not to imply production checkout or live payment readiness. |
| Operations/on-call/support | Monitoring, rollback, incident path, and support owner approved. |
| Backup/RPO | No new stateful dependency introduced by discovery metadata exposure. |
| Named merchant | Merchant owner approval covers any merchant referenced in AgenticOrg metadata. |

## No-Go Conditions

Do not enable AgenticOrg public commerce discovery if any condition below is
true:

- Grantex read-only production discovery remains disabled or failed smoke.
- Grantex discovery was enabled by the broad Commerce runtime gate instead of a
  narrow read-only gate.
- Grantex named merchant approval is missing.
- Grantex discovery payload contains secrets, provider credentials, live payment
  language, live Plural language, or readiness/certification overclaims.
- AgenticOrg metadata would advertise direct Stripe, Plural, Pine, or provider
  credential handling.
- AgenticOrg metadata would imply production checkout/payment readiness,
  external pilot readiness, AP2/UCP/ACP certification, provider certification,
  or Plural certification.
- There is no rollback owner or rollback checklist.
- Security, legal/compliance, product wording, operations, backup/RPO, named
  merchant, or AgenticOrg dependency approval is incomplete.

## AgenticOrg Read-Only Smoke Checklist

This smoke is for a separate future AgenticOrg approval after Grantex read-only
smoke passes. It must use read-only GET requests only.

| Check | Expected result |
| --- | --- |
| Health | `GET https://app.agenticorg.ai/api/v1/health` returns 200. |
| MCP tools before enablement | Public tools hide `agenticorg_commerce_sales_agent`. |
| A2A agent card before enablement | Public card hides commerce metadata. |
| A2A agents before enablement | Public agents hide `commerce_sales_agent` and `grantex_commerce:*`. |
| Grantex dependency | Grantex read-only discovery is already approved, enabled, and smoke-tested. |
| Public metadata after separate approval | If later approved, metadata remains Grantex-only and references approved Grantex discovery posture. |
| Provider scan | No direct Stripe, Plural, Pine, provider credential, secret, DB/Redis URL, private key, or raw payload markers. |
| Wording scan | No live-payment, live-Plural, production-ready, external-pilot-ready, or certification overclaims. |
| Rollback | Disabling the AgenticOrg discovery gate hides commerce metadata again. |

## Rollback Plan

If a later AgenticOrg public discovery rollout is approved and then needs
rollback:

1. Disable or unset `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED`.
2. Redeploy or revise only if the platform requires it for config-name changes.
3. Confirm public MCP tools hide `agenticorg_commerce_sales_agent`.
4. Confirm public A2A card/list hide `commerce_sales_agent` and
   `grantex_commerce:*`.
5. Confirm health and non-commerce discovery remain available.
6. Confirm Grantex read-only discovery posture is unchanged or rolled back as
   intended.
7. Record summarized evidence only; do not record raw payloads or secret values.

## Recommendation

Keep AgenticOrg public commerce discovery hidden through the Grantex C5D
read-only discovery rollout. Do not enable AgenticOrg public commerce metadata
until Grantex read-only discovery has passed smoke and a separate AgenticOrg
approval explicitly authorizes public MCP/A2A commerce discovery.

## Future AgenticOrg Prompt

Do not run this prompt until Grantex read-only discovery is approved, enabled,
and smoke-tested.

```text
Task: C5E approved AgenticOrg public commerce discovery rollout after Grantex read-only discovery passes.

Approved:
- Enable only AgenticOrg public commerce discovery metadata.
- Keep commerce execution Grantex-only.
- Do not enable checkout/payment creation.
- Do not enable live payments.
- Do not enable live Plural.
- Do not add provider credential paths.
- Do not run state-changing production requests.

Preconditions:
1. Grantex read-only production Commerce discovery passed smoke.
2. Grantex named merchant approval is complete.
3. Grantex payload passed secret and overclaim scans.
4. Security, legal/compliance, product wording, operations, backup/RPO, named merchant, and AgenticOrg dependency approvals are complete.

Rollout:
1. Apply only the approved AgenticOrg discovery config name:
   - `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED`
2. Deploy or revise only if the platform requires it.
3. Do not change secrets, provider flags, checkout/payment flags, live payment flags, or live Plural flags.

Read-only smoke:
1. GET health.
2. GET MCP tools.
3. GET A2A agent card.
4. GET A2A agents.
5. Confirm commerce metadata is Grantex-only and contains no provider credential path.
6. Confirm no live-payment, live-Plural, production-ready, external-pilot-ready, or certification overclaims.

Rollback:
1. Disable or unset `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED`.
2. Confirm public MCP/A2A discovery hides commerce metadata.
```
