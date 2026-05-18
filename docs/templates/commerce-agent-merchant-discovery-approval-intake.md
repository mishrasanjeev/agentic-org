# AgenticOrg Commerce Merchant Discovery Approval Intake Template

Status: reusable intake template only
Scope: future human artifact intake for AgenticOrg dependency on Grantex named
merchant read-only discovery
Production changes authorized by this template: none
AgenticOrg commerce public discovery authorized by this template: none
Merchant approval granted by this template: none

Use this template to collect AgenticOrg dependency artifacts for a future public
commerce discovery review after Grantex read-only merchant discovery is
approved and smoke-tested. This template does not approve a merchant, approve
configuration, set an allowlist value, or approve a production rollout.
Placeholders are not approval.

## Merchant Placeholder Fields

| Field | Intake value | Reviewer notes |
| --- | --- | --- |
| Grantex merchant identifier | `<MERCHANT_ID_PENDING_APPROVAL>` | Pending |
| Public display name | `<MERCHANT_PUBLIC_NAME_PENDING_APPROVAL>` | Pending |
| Legal or entity reference | `<MERCHANT_LEGAL_ENTITY_PENDING_APPROVAL>` | Pending |
| Public category | `<MERCHANT_CATEGORY_PENDING_APPROVAL>` | Pending |

## Approval Artifact Intake

| Artifact | Evidence reference | Approver | Date | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Merchant owner approval | `<ARTIFACT_REFERENCE_PENDING>` | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | Pending | Grantex merchant owner approval is not provided. |
| Legal/compliance approval | `<ARTIFACT_REFERENCE_PENDING>` | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | Pending | AgenticOrg public metadata review remains pending. |
| Product wording approval | `<ARTIFACT_REFERENCE_PENDING>` | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | Pending | Grantex-only wording review remains pending. |
| Security approval | `<ARTIFACT_REFERENCE_PENDING>` | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | Pending | MCP/A2A non-secret metadata and provider-boundary review remains pending. |
| Ops/on-call/support approval | `<ARTIFACT_REFERENCE_PENDING>` | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | Pending | AgenticOrg support, smoke, incident, and rollback ownership remain pending. |
| Backup/RPO approval | `<ARTIFACT_REFERENCE_PENDING>` | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | Pending | Backup and recovery posture review remains pending. |
| AgenticOrg dependency approval | `<ARTIFACT_REFERENCE_PENDING>` | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | Pending | Separate AgenticOrg approval remains pending. |

## Public Discovery Profile Review

| Profile field | Proposed value | Required review | Status |
| --- | --- | --- | --- |
| Display name | `<MERCHANT_PUBLIC_NAME_PENDING_APPROVAL>` | Legal/compliance, product wording | Pending |
| Legal/entity reference | `<MERCHANT_LEGAL_ENTITY_PENDING_APPROVAL>` | Legal/compliance | Pending |
| Category | `<MERCHANT_CATEGORY_PENDING_APPROVAL>` | Legal/compliance, product wording | Pending |
| Description | `<PUBLIC_DESCRIPTION_PENDING_APPROVAL>` | Legal/compliance, product wording | Pending |
| Supported capabilities | `<SUPPORTED_CAPABILITIES_PENDING_APPROVAL>` | Security, product wording | Pending |
| Support contact | `<SUPPORT_CONTACT_PENDING_APPROVAL>` | Ops/support, legal/compliance | Pending |
| Escalation path | `<ESCALATION_PATH_PENDING_APPROVAL>` | Ops/on-call/support | Pending |
| Issuer/JWKS references | `<ISSUER_JWKS_REFERENCE_PENDING_APPROVAL>` | Security | Pending |
| Discovery posture wording | `<DISCOVERY_POSTURE_WORDING_PENDING_APPROVAL>` | Security, product wording | Pending |
| No-go wording | `<NO_GO_WORDING_PENDING_APPROVAL>` | Security, legal/compliance, product wording | Pending |

## Required Evidence Intake

| Evidence | Reference | Status | Notes |
| --- | --- | --- | --- |
| C5C deployed gate evidence | `<EVIDENCE_REFERENCE_PENDING>` | Pending | Confirms Grantex has a narrow discovery gate. |
| C5D rollout plan | `<EVIDENCE_REFERENCE_PENDING>` | Pending | Confirms AgenticOrg stays hidden through Grantex rollout planning. |
| C5E named merchant approval package | `<EVIDENCE_REFERENCE_PENDING>` | Pending | Confirms merchant approval package exists but needs human completion. |
| C5F artifact checklist | `<EVIDENCE_REFERENCE_PENDING>` | Pending | Confirms this intake maps to the current checklist. |
| Approved public payload preview | `<EVIDENCE_REFERENCE_PENDING>` | Pending | Must be Grantex-reviewed before AgenticOrg references it. |
| No-go checklist | `<EVIDENCE_REFERENCE_PENDING>` | Pending | Must show no AgenticOrg exposure blocker is present. |
| Rollback checklist | `<EVIDENCE_REFERENCE_PENDING>` | Pending | Must name AgenticOrg rollback owner and hide-discovery steps. |
| Read-only smoke checklist | `<EVIDENCE_REFERENCE_PENDING>` | Pending | Must cover AgenticOrg health, MCP, A2A, marker scans, and Grantex-only metadata. |

## Ownership Intake

| Role | Owner | Date | Status |
| --- | --- | --- | --- |
| Rollback owner | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | Pending |
| Read-only smoke owner | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | Pending |
| Support owner | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | Pending |
| Incident escalation owner | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | Pending |
| Evidence retention owner | `<APPROVER_NAME_PENDING>` | `<APPROVAL_DATE_PENDING>` | Pending |

## Decision

| Decision state | Selected | Meaning |
| --- | --- | --- |
| Not approved | Yes | Required Grantex and AgenticOrg artifacts are not complete. |
| Approved for internal review only | No | Metadata may be reviewed internally, but not published. |
| Approved for public read-only discovery | No | Metadata may be used only in a later separately approved rollout. |

## Hard Blockers

- Missing named Grantex merchant approval remains a hard blocker.
- No config value is approved by this intake.
- No allowlist value is approved by this intake.
- No production rollout is approved by this intake.
- Completed intake still requires separate AgenticOrg rollout approval.
- AgenticOrg public commerce discovery remains hidden pending Grantex
  read-only smoke and separate AgenticOrg approval.
