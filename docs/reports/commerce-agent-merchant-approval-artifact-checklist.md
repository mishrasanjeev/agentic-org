# AgenticOrg Commerce Merchant Approval Artifact Checklist

Status: planning checklist only
Date: 2026-05-18
Scope: AgenticOrg dependency artifact collection for a future Grantex named
merchant read-only production Commerce discovery review
Production changes made by this checklist: none
AgenticOrg commerce public discovery enabled by this checklist: no
Grantex production Commerce V1 enabled by this checklist: no
Checkout or payment creation enabled by this checklist: no
Live payments enabled by this checklist: no
Live Plural enabled by this checklist: no
Named merchant approved by this checklist: no
Config values approved by this checklist: no
Secrets inspected or changed: no

This checklist records the AgenticOrg artifacts that must exist before a future
human review can consider public AgenticOrg commerce discovery for a Grantex
named merchant. It does not approve a merchant, enable AgenticOrg public
commerce discovery, or authorize a production rollout.

## Placeholder Rules

All merchant and approver fields in this packet are placeholders. They are not
approval, and they must not be copied into production configuration.

| Placeholder | Required meaning |
| --- | --- |
| `<MERCHANT_ID_PENDING_APPROVAL>` | Grantex merchant identifier pending human approval. |
| `<MERCHANT_PUBLIC_NAME_PENDING_APPROVAL>` | Public merchant display name pending human approval. |
| `<MERCHANT_LEGAL_ENTITY_PENDING_APPROVAL>` | Legal or entity reference pending human approval. |
| `<MERCHANT_CATEGORY_PENDING_APPROVAL>` | Public category label pending human approval. |
| `<APPROVER_NAME_PENDING>` | Named human approver pending assignment. |
| `<APPROVAL_DATE_PENDING>` | Approval date pending signoff. |

## Artifact Collection Checklist

| Artifact | Required evidence | Owner placeholder | Status |
| --- | --- | --- | --- |
| Merchant owner approval | Grantex approval record for `<MERCHANT_ID_PENDING_APPROVAL>` and `<MERCHANT_PUBLIC_NAME_PENDING_APPROVAL>`. | `<APPROVER_NAME_PENDING>` | Pending |
| Legal/compliance approval | Confirmation that AgenticOrg can display only reviewed Grantex-controlled commerce metadata for the named merchant. | `<APPROVER_NAME_PENDING>` | Pending |
| Product wording approval | Confirmation that AgenticOrg wording remains Grantex-only and avoids checkout, payment, live payment, live Plural, readiness, and certification overclaims. | `<APPROVER_NAME_PENDING>` | Pending |
| Security approval | Confirmation that public MCP/A2A metadata contains no secrets, no direct provider path, and no runtime consent or payment material. | `<APPROVER_NAME_PENDING>` | Pending |
| Ops/on-call/support approval | Named support path, rollback owner, smoke owner, and incident escalation owner for AgenticOrg public discovery metadata. | `<APPROVER_NAME_PENDING>` | Pending |
| Backup/RPO approval | Confirmation that enabling or rolling back public metadata does not affect backup or recovery posture. | `<APPROVER_NAME_PENDING>` | Pending |
| AgenticOrg dependency approval | Confirmation that AgenticOrg remains hidden until Grantex read-only discovery is approved, enabled, and smoke-tested. | `<APPROVER_NAME_PENDING>` | Pending |

## Public Discovery Profile Fields Requiring Review

AgenticOrg must review the Grantex-sourced merchant metadata it may later
reference. If a field is incomplete, unreviewed, or rejected, AgenticOrg public
commerce discovery remains hidden.

| Field | Placeholder or posture | Required reviewers |
| --- | --- | --- |
| Display name | `<MERCHANT_PUBLIC_NAME_PENDING_APPROVAL>` | Product wording, legal/compliance |
| Legal/entity reference | `<MERCHANT_LEGAL_ENTITY_PENDING_APPROVAL>` or omitted | Legal/compliance |
| Category | `<MERCHANT_CATEGORY_PENDING_APPROVAL>` | Product wording, legal/compliance |
| Description | Approved public wording sourced from the Grantex review packet | Product wording, legal/compliance |
| Supported capabilities | Grantex-only read-only discovery metadata; no direct checkout or payment creation promise | Product wording, security |
| Support contact | Approved public support channel only | Ops/support, legal/compliance |
| Escalation path | Approved support or incident escalation reference, as applicable | Ops/on-call/support |
| Issuer/JWKS references | Public Grantex verification references only | Security |
| Discovery posture wording | Must say AgenticOrg exposes Grantex-controlled metadata only after separate approval | Product wording, security |
| No-go wording | Must clearly block direct provider handling, live payments, live Plural, readiness claims, and certification claims | Security, product wording, legal/compliance |

## Required Evidence Packet

The future AgenticOrg approval record must attach or link these artifacts before
any AgenticOrg public commerce discovery request can proceed:

| Evidence | Required status |
| --- | --- |
| C5C deployed Grantex gate evidence | Confirms Grantex has a deployed narrow read-only discovery gate. |
| C5D rollout plan | Confirms the Grantex rollout plan keeps AgenticOrg public commerce discovery hidden until Grantex smoke passes. |
| C5E named merchant approval package | Confirms the named merchant package exists and remains placeholder-only until humans approve it. |
| Approved public payload preview | Grantex-reviewed preview that AgenticOrg may reference after separate approval. |
| No-go checklist | Completed checklist showing no blocking condition is present for AgenticOrg exposure. |
| Rollback checklist | Named AgenticOrg rollback owner and verified steps to hide commerce metadata again. |
| Read-only smoke checklist | Planned GET-only checks for AgenticOrg health, MCP tools, A2A agent card, A2A agents, marker scans, and Grantex-only metadata. |

## Decision States

One decision must be selected by authorized humans later. Until that happens,
the decision is not approved.

| Decision | Meaning | AgenticOrg public commerce discovery permitted |
| --- | --- | --- |
| Not approved | Grantex named merchant artifacts or AgenticOrg dependency artifacts are missing, rejected, or still placeholder-only. | No |
| Approved for internal review only | AgenticOrg metadata can be reviewed internally but not exposed publicly. | No |
| Approved for public read-only discovery | AgenticOrg may expose reviewed Grantex-only metadata in a later approved rollout. | Not by this checklist; a separate rollout approval is still required |

Selected decision: Not approved

Reason: `<APPROVAL_REASON_PENDING>`

## No-Go Conditions

Do not proceed if any condition below is true:

- Named Grantex merchant approval is missing.
- The artifact packet still contains unresolved merchant placeholders.
- Broad `COMMERCE_V1_ENABLED` would be required.
- Any checkout or payment route would become enabled.
- A live payment or live Plural flag would be true.
- Provider credentials would be exposed or referenced as public metadata.
- Public wording would imply readiness, certification, external pilot approval,
  live payment approval, live Plural approval, or provider approval.
- `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED` would be enabled before
  Grantex named merchant read-only smoke passes.
- AgenticOrg would expose direct Stripe, Plural, Pine, or provider credential
  handling.
- Rollback owner, read-only smoke owner, support owner, incident escalation
  owner, or evidence retention owner is missing.

## Ownership Fields

These ownership rows must be completed by named humans before a later
AgenticOrg rollout request can be approved.

| Role | Owner | Date | Notes |
| --- | --- | --- | --- |
| Rollback owner | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | `<APPROVAL_NOTE_PENDING>` |
| Read-only smoke owner | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | `<APPROVAL_NOTE_PENDING>` |
| Support owner | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | `<APPROVAL_NOTE_PENDING>` |
| Incident escalation owner | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | `<APPROVAL_NOTE_PENDING>` |
| Evidence retention owner | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | `<APPROVAL_NOTE_PENDING>` |

## AgenticOrg Dependency

AgenticOrg commerce public discovery remains hidden until Grantex read-only
discovery is approved for a named merchant and smoke-tested successfully.

AgenticOrg requires separate approval before enabling
`AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED`. This checklist does not approve
that setting.

## Explicit Status

- No merchant is approved by this packet.
- Placeholders are not approval.
- No config value is approved by this packet.
- No production rollout is approved by this packet.
- `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED` remains disabled unless a
  later approved AgenticOrg rollout changes it.
- `COMMERCE_PUBLIC_DISCOVERY_ENABLED` and
  `COMMERCE_PUBLIC_DISCOVERY_MERCHANT_ALLOWLIST` remain Grantex-side controls
  that require separate Grantex approval and smoke evidence.
- AgenticOrg remains a Grantex-only commerce integration and must not add a
  direct provider credential path.
