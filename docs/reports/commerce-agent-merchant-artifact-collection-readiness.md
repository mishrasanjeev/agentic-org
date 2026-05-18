# AgenticOrg Commerce Merchant Artifact Collection Readiness

Status: planning packet only
Scope: human-facing AgenticOrg dependency artifact collection instructions for a
future Grantex named merchant read-only discovery review
Production changes made by this packet: none
AgenticOrg commerce public discovery enabled by this packet: no
Grantex production Commerce V1 enabled by this packet: no
Checkout or payment creation enabled by this packet: no
Live payments enabled by this packet: no
Live Plural enabled by this packet: no
Merchant approved by this packet: no
Config values approved by this packet: no
Allowlist values approved by this packet: no

This packet tells reviewers what AgenticOrg dependency artifacts to provide and
where each artifact maps in the C5G intake template. It does not approve a
merchant, approve any configuration, enable AgenticOrg public commerce
discovery, or authorize production rollout. Placeholders are not approval.

## Public and Private Artifact Policy

| Artifact type | Handling rule |
| --- | --- |
| Public-safe summary | May be committed when it contains only non-secret status, placeholder fields, high-level decision state, and non-secret reference IDs. |
| Private signed approval artifacts | Must stay outside the repository. Commit only a non-secret reference such as `<PRIVATE_APPROVAL_REFERENCE_PENDING>`. |
| Private commercial records | Must stay outside the repository. Do not commit private agreements, private account terms, pricing, or sensitive business details. |
| Private contacts | Must stay outside the repository. Do not commit personal contact details, phone numbers, direct email addresses, or private escalation channels. |
| Sensitive operational details | Must stay outside the repository. Do not commit secrets, provider credentials, raw payloads, DB/Redis URLs, or runtime tokens. |

Private artifacts may be referenced only by non-secret ticket or record
identifiers. The reference itself must not reveal private business details.

## Placeholder Fields

These placeholders must remain until explicit human artifacts replace them
outside this packet:

| Placeholder | Meaning |
| --- | --- |
| `<MERCHANT_ID_PENDING_APPROVAL>` | Grantex merchant identifier pending approval. |
| `<MERCHANT_PUBLIC_NAME_PENDING_APPROVAL>` | Public merchant display name pending approval. |
| `<MERCHANT_LEGAL_ENTITY_PENDING_APPROVAL>` | Legal or entity reference pending approval. |
| `<MERCHANT_CATEGORY_PENDING_APPROVAL>` | Public category pending approval. |
| `<APPROVER_NAME_PENDING>` | Human approver pending assignment. |
| `<APPROVAL_DATE_PENDING>` | Approval date pending signoff. |
| `<PRIVATE_APPROVAL_REFERENCE_PENDING>` | Non-secret reference to a private artifact held outside the repo. |

## Artifact Submission Checklist

| Required artifact | What reviewers provide | Public repo handling | Current status |
| --- | --- | --- | --- |
| Merchant owner approval | Grantex private signed approval for `<MERCHANT_ID_PENDING_APPROVAL>` and `<MERCHANT_PUBLIC_NAME_PENDING_APPROVAL>`. | Commit only `<PRIVATE_APPROVAL_REFERENCE_PENDING>` and a public-safe summary. | Missing |
| Legal/compliance approval | Review that AgenticOrg can expose only reviewed Grantex-controlled metadata. | Commit only non-secret status and `<PRIVATE_APPROVAL_REFERENCE_PENDING>`. | Missing |
| Product wording approval | Review that AgenticOrg copy remains Grantex-only and avoids rollout, certification, live provider, checkout, and payment overclaims. | Commit approved summary only after review. | Missing |
| Security approval | Review that MCP/A2A metadata contains no secrets, no provider path, and no runtime consent or payment material. | Commit high-level pass/fail status only. | Missing |
| Ops/on-call/support approval | Owner assignments for AgenticOrg support path, smoke, incident escalation, rollback, and evidence retention. | Commit role names only when approved for public docs; otherwise commit `<PRIVATE_APPROVAL_REFERENCE_PENDING>`. | Missing |
| Backup/RPO approval | Review that public metadata and rollback do not alter backup or recovery posture. | Commit non-secret status only. | Missing |
| AgenticOrg dependency approval | Separate AgenticOrg approval after Grantex read-only smoke passes. | Commit non-secret status only. | Missing |
| Approved public payload preview | Grantex-reviewed public discovery metadata preview that AgenticOrg may reference. | Commit only a sanitized preview after approval. | Missing |
| No-go checklist | Completed checklist proving no AgenticOrg exposure blocker is present. | Commit public-safe checklist summary. | Missing |
| Rollback checklist | Verified steps and owner for hiding AgenticOrg commerce metadata again. | Commit public-safe steps and non-secret owner reference. | Missing |
| Read-only smoke checklist | Planned GET-only checks for health, MCP tools, A2A card, A2A agents, and marker scans. | Commit public-safe checklist summary. | Missing |

## Artifact-to-Template Mapping

| Artifact | C5G intake template section | Status |
| --- | --- | --- |
| Merchant owner approval | `Approval Artifact Intake` / `Merchant owner approval` | Missing |
| Legal/compliance approval | `Approval Artifact Intake` / `Legal/compliance approval` | Missing |
| Product wording approval | `Approval Artifact Intake` / `Product wording approval` | Missing |
| Security approval | `Approval Artifact Intake` / `Security approval` | Missing |
| Ops/on-call/support approval | `Approval Artifact Intake` / `Ops/on-call/support approval` | Missing |
| Backup/RPO approval | `Approval Artifact Intake` / `Backup/RPO approval` | Missing |
| AgenticOrg dependency approval | `Approval Artifact Intake` / `AgenticOrg dependency approval` | Missing |
| Approved public payload preview | `Required Evidence Intake` / `Approved public payload preview` | Missing |
| No-go checklist | `Required Evidence Intake` / `No-go checklist` | Missing |
| Rollback checklist | `Required Evidence Intake` / `Rollback checklist` | Missing |
| Read-only smoke checklist | `Required Evidence Intake` / `Read-only smoke checklist` | Missing |
| Rollback owner | `Ownership Intake` / `Rollback owner` | Missing |
| Read-only smoke owner | `Ownership Intake` / `Read-only smoke owner` | Missing |
| Support owner | `Ownership Intake` / `Support owner` | Missing |
| Incident escalation owner | `Ownership Intake` / `Incident escalation owner` | Missing |
| Evidence retention owner | `Ownership Intake` / `Evidence retention owner` | Missing |

## Current Blocker Status

Missing named Grantex merchant approval remains the hard blocker. No submitted
artifact currently replaces:

- `<MERCHANT_ID_PENDING_APPROVAL>`
- `<MERCHANT_PUBLIC_NAME_PENDING_APPROVAL>`
- `<MERCHANT_LEGAL_ENTITY_PENDING_APPROVAL>`
- `<MERCHANT_CATEGORY_PENDING_APPROVAL>`
- `<APPROVER_NAME_PENDING>`
- `<APPROVAL_DATE_PENDING>`

## Submission Process

1. Collect Grantex private signed approvals outside the repository.
2. Assign each private artifact a non-secret reference:
   `<PRIVATE_APPROVAL_REFERENCE_PENDING>`.
3. Prepare a public-safe AgenticOrg summary for each artifact with no private
   contacts, private agreement terms, sensitive business details, or runtime
   material.
4. Fill the C5G AgenticOrg intake template with only public-safe summaries and
   non-secret references.
5. Verify the Grantex public payload preview before AgenticOrg references it.
6. Complete the AgenticOrg no-go checklist, rollback checklist, and read-only
   smoke checklist.
7. Keep AgenticOrg public commerce discovery hidden until Grantex read-only
   smoke passes and separate AgenticOrg approval exists.

## Future Handoff Checklist

| Handoff item | Status |
| --- | --- |
| Artifact packet received | Missing |
| Private references verified by human reviewer | Missing |
| Public payload preview prepared | Missing |
| No-go checklist passed | Missing |
| Signoffs complete | Missing |
| Rollout PR or prompt prepared separately | Missing |

## Explicit Non-Approval

- No merchant is approved.
- Placeholders are not approval.
- No config value is approved.
- No allowlist value is approved.
- No production rollout is approved.
- Completed artifact collection still requires separate AgenticOrg rollout
  approval.
- AgenticOrg public commerce discovery remains hidden pending Grantex
  read-only smoke and separate AgenticOrg approval.
