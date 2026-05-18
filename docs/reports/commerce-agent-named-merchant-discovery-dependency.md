# AgenticOrg Named Merchant Discovery Dependency

Status: planning template only
Date: 2026-05-18
Scope: AgenticOrg dependency on a future named merchant Grantex read-only discovery approval
Production changes made by this package: none
AgenticOrg commerce public discovery enabled by this package: no
Grantex production Commerce V1 enabled by this package: no
Checkout or payment creation enabled by this package: no
Live payments enabled by this package: no
Live Plural enabled by this package: no
Named production merchant approved by this package: no
Secrets inspected or changed: no

This document records the AgenticOrg dependency on a future Grantex named
merchant approval. It is a planning template only and does not approve or enable
AgenticOrg public commerce discovery.

## Dependency Blocker

AgenticOrg public commerce discovery remains blocked until Grantex has a named
merchant approval package and a successful read-only production discovery smoke.

No production merchant ID or merchant name is approved by this package. Any
merchant references must use placeholders:

| Placeholder | Meaning |
| --- | --- |
| `<MERCHANT_ID_PENDING_APPROVAL>` | Grantex merchant identifier pending approval. |
| `<MERCHANT_PUBLIC_NAME_PENDING_APPROVAL>` | Public merchant name pending approval. |
| `<APPROVER_NAME_PENDING>` | Human approver placeholder. |
| `<APPROVAL_DATE_PENDING>` | Approval date placeholder. |

## Required Grantex Inputs

AgenticOrg can consider public commerce metadata only after Grantex provides
all inputs below:

| Input | Required status |
| --- | --- |
| Named merchant approval | Complete for `<MERCHANT_ID_PENDING_APPROVAL>`. |
| Public merchant name approval | Complete for `<MERCHANT_PUBLIC_NAME_PENDING_APPROVAL>`. |
| Grantex read-only discovery smoke | Passed against the approved merchant profile. |
| Secret scan | Discovery payload contains no secret or runtime material. |
| Wording scan | Discovery payload contains no readiness, live-payment, live-Plural, external-pilot, or certification overclaims. |
| Rollback owner | `<APPROVER_NAME_PENDING>` is named before any public exposure. |

## AgenticOrg Public Metadata Review

If Grantex later passes named merchant read-only smoke, AgenticOrg still needs a
separate public discovery approval. That approval must confirm:

- Public MCP discovery can expose commerce metadata only after separate
  approval.
- Public A2A discovery can expose commerce metadata only after separate
  approval.
- The Commerce Sales Agent remains Grantex-only.
- AgenticOrg does not advertise direct Stripe, Plural, Pine, or provider
  credential handling.
- AgenticOrg does not imply production checkout/payment readiness.
- AgenticOrg does not imply live payment readiness.
- AgenticOrg does not imply live Plural readiness.
- AgenticOrg does not imply external pilot readiness.
- AgenticOrg does not imply AP2, UCP, ACP, provider, or Plural certification.

## AgenticOrg Dependency Gate

AgenticOrg must remain hidden until both conditions are true:

1. Grantex read-only smoke passes for the approved named merchant.
2. A separate AgenticOrg approval explicitly authorizes public commerce
   discovery.

Required posture:

| Phase | AgenticOrg public commerce discovery posture |
| --- | --- |
| Before Grantex merchant approval | Hidden |
| During Grantex read-only rollout | Hidden |
| After Grantex read-only smoke passes | Hidden until separate AgenticOrg approval |
| After separate AgenticOrg approval | Eligible for a later read-only public discovery rollout |

## Exact Config Names

This package records config names only, not values:

- `COMMERCE_PUBLIC_DISCOVERY_ENABLED`
- `COMMERCE_PUBLIC_DISCOVERY_MERCHANT_ALLOWLIST`
- `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED`

## Human Approval Checklist

Before a later AgenticOrg public commerce discovery rollout:

| Gate | Required approval |
| --- | --- |
| Grantex merchant owner | `<APPROVER_NAME_PENDING>` approves the named merchant profile. |
| Grantex security | `<APPROVER_NAME_PENDING>` confirms non-secret discovery metadata. |
| Grantex legal/compliance | `<APPROVER_NAME_PENDING>` approves merchant and consent/payment wording. |
| Grantex product wording | `<APPROVER_NAME_PENDING>` approves capability language. |
| Grantex operations | `<APPROVER_NAME_PENDING>` owns rollback and evidence retention. |
| AgenticOrg security | `<APPROVER_NAME_PENDING>` approves MCP/A2A public metadata exposure. |
| AgenticOrg product wording | `<APPROVER_NAME_PENDING>` approves Grantex-only commerce language. |
| AgenticOrg operations | `<APPROVER_NAME_PENDING>` owns AgenticOrg rollback and smoke evidence. |

## No-Go Conditions

Do not enable AgenticOrg public commerce discovery if any condition below is
true:

- Grantex merchant approval still uses placeholders.
- No named Grantex merchant owner approval exists.
- Grantex read-only discovery smoke has not passed.
- Grantex discovery payload contains secrets, provider credentials, live
  payment language, live Plural language, or overclaims.
- AgenticOrg would expose `grantex_commerce:*` metadata before a separate
  AgenticOrg approval exists.
- AgenticOrg would advertise direct provider credential handling.
- AgenticOrg would imply production-ready, live-payment-ready,
  live-Plural-ready, external-pilot-ready, AP2/UCP/ACP certification, provider
  certification, or Plural certification status.
- Rollback owner or smoke owner is missing.

## Decision Record

One decision must be selected later. Until then, the default decision is not
approved.

| Decision | Meaning | AgenticOrg public commerce discovery permitted |
| --- | --- | --- |
| Not approved | Grantex named merchant approval or AgenticOrg approval is incomplete. | No |
| Approved for internal review only | Metadata can be reviewed internally but not exposed publicly. | No |
| Approved for public read-only discovery | AgenticOrg may expose reviewed Grantex-only commerce metadata in a later approved rollout. | Not by this package; requires separate rollout approval |

Selected decision: Not approved

Reason: `<APPROVAL_REASON_PENDING>`

## Future AgenticOrg Approved-Run Prompt

Do not run this prompt until Grantex named merchant read-only discovery is
approved, enabled, and smoke-tested.

```text
Task: C5G approved AgenticOrg public commerce discovery rollout after Grantex named merchant discovery passes.

Approved:
- Enable only AgenticOrg public commerce discovery metadata.
- Keep commerce execution Grantex-only.
- Do not enable checkout/payment creation.
- Do not enable live payments.
- Do not enable live Plural.
- Do not add provider credential paths.
- Do not run state-changing production requests.

Preconditions:
1. Grantex named merchant approval record is signed.
2. Grantex read-only discovery smoke passed for <MERCHANT_ID_PENDING_APPROVAL>.
3. Grantex payload passed secret and overclaim scans.
4. AgenticOrg security, product wording, operations, and dependency approval records are signed.

Rollout:
1. Apply only the approved AgenticOrg discovery config name:
   - AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED
2. Deploy or revise only if the platform requires it.
3. Do not change secrets, checkout/payment settings, live payment settings, live Plural settings, or provider credentials.

Read-only smoke:
1. GET health.
2. GET MCP tools.
3. GET A2A agent card.
4. GET A2A agents.
5. Confirm commerce metadata is Grantex-only and references the approved Grantex discovery posture.
6. Confirm no provider credential, secret, live-payment, live-Plural, or overclaim markers.

Rollback:
1. Disable AgenticOrg public commerce discovery configuration.
2. Confirm public MCP/A2A discovery hides commerce metadata.
```
