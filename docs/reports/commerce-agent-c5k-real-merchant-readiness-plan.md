# Commerce Agent C5K Real Merchant Readiness Plan

Status: historical planning artifact; superseded by the current OACP runtime path in docs/oacp-end-to-end-flow.md.
Date: 2026-05-26
Scope: AgenticOrg bridge from C5I/C5J synthetic smoke success to a future real
named Grantex merchant approval intake
Production changes made by this plan: none
AgenticOrg public commerce discovery changed by this plan: no
Grantex production Commerce V1 changed by this plan: no
Merchant allowlist value approved by this plan: no
Checkout or payment creation changed by this plan: no
Live payment path changed by this plan: no
Live Plural path changed by this plan: no
Named merchant approved by this plan: no
Secrets inspected or changed: no

This record defines the AgenticOrg dependency path after C5I and C5J. It does
not approve a real merchant, approve AgenticOrg public discovery, approve a
Grantex production allowlist entry, or authorize any rollout.

## Current State

- Grantex C5J evidence is merged at
  `37fa71b4192398085daf9e07f53342b46882b578`.
- AgenticOrg C5J evidence is merged at
  `5f2a61513ba9aabf56ab6393de20e1493f562d25`.
- C5I synthetic dataset work is merged.
- C5J local synthetic smoke evidence is merged.
- No real named merchant approval exists.
- Grantex production read-only discovery remains fail-closed.
- AgenticOrg public commerce discovery remains gated.
- `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED` remains disabled.
- `COMMERCE_PUBLIC_DISCOVERY_ENABLED` remains disabled.
- `COMMERCE_PUBLIC_DISCOVERY_MERCHANT_ALLOWLIST` has no approved production
  value from this plan.
- Real C5I artifact intake remains blocked until human approvals are provided.

## What C5I And C5J Proved

- The C5I synthetic dataset validators work against the internal synthetic
  dataset shape.
- The local Grantex synthetic discovery-gate tests pass.
- The AgenticOrg gated discovery and no-provider-call tests pass.
- AgenticOrg remains hidden by default for public commerce discovery.
- The synthetic dataset remains internal, local, and smoke-only.
- The synthetic path can verify AgenticOrg guardrails without requiring real
  merchant details, provider credentials, production config, or production
  allowlist values.

## What C5I And C5J Did Not Prove

- They did not prove real merchant approval.
- They did not approve production discovery.
- They did not approve a production merchant allowlist value.
- They did not prove checkout, payment creation, live payment, live Plural, or
  live provider readiness.
- They did not approve AgenticOrg public commerce discovery.
- They did not convert synthetic merchant IDs, names, or payloads into
  production evidence.

## Required Real Merchant Artifacts

All artifacts must be collected from authorized humans outside the repository.
AgenticOrg may record only public-safe summaries and non-secret private
references after review.

| Artifact | Required evidence | Current state |
| --- | --- | --- |
| Approved public merchant ID | A Grantex-approved public identifier for one real merchant. | Pending |
| Approved public merchant display name | A reviewed public display name for the same merchant. | Pending |
| Approved category | A reviewed public category label. | Pending |
| Approved public discovery description | Reviewed public wording for read-only discovery metadata. | Pending |
| Merchant owner approval | Written approval that AgenticOrg may reference the reviewed Grantex-controlled merchant metadata. | Pending |
| Legal/compliance approval | Approval covering AgenticOrg exposure of public Grantex-controlled commerce metadata. | Pending |
| Product wording approval | Approval that AgenticOrg wording avoids readiness, certification, checkout, payment, live provider, and production overclaims. | Pending |
| Security approval | Approval that AgenticOrg exposes no secrets, private details, provider credentials, direct provider paths, or payment material. | Pending |
| Ops/on-call/support approval | Named support, monitoring, on-call, incident, and escalation ownership. | Pending |
| Backup/RPO approval | Confirmation that AgenticOrg metadata exposure and rollback do not weaken backup or recovery posture. | Pending |
| AgenticOrg dependency approval | Separate approval confirming the Grantex merchant path has completed approval and AgenticOrg may expose only reviewed Grantex-controlled metadata. | Pending |
| Rollback owner | Named owner for hiding AgenticOrg commerce metadata again. | Pending |
| Read-only smoke owner | Named owner for future AgenticOrg GET-only public discovery smoke evidence. | Pending |
| Support owner | Named owner for AgenticOrg support response. | Pending |
| Incident owner | Named owner for AgenticOrg incident escalation and triage. | Pending |
| Evidence owner | Named owner for AgenticOrg evidence retention and scrubbed proof records. | Pending |

## Public Payload Preview Review

A future C5K readiness review must preview the exact AgenticOrg public payload
before any rollout request. The preview must use approved Grantex-controlled
merchant values only and must not include secrets, private merchant data,
provider credentials, direct provider endpoints, live payment claims, live
Plural claims, provider certification claims, or production readiness claims.

Fields requiring exact review:

- `merchant_id`
- `display_name`
- `category`
- `public_discovery_description`
- `agent_card_commerce_summary`
- `mcp_tool_discovery_summary`
- `supported_capabilities`
- `discovery_posture`
- `support_contact_policy`
- `incident_escalation_policy`
- `grantex_verification_reference`
- `cache_headers`
- `rollback_owner_reference`
- `read_only_smoke_owner_reference`
- `evidence_owner_reference`

Allowed posture for the preview: AgenticOrg may expose only read-only,
Grantex-controlled discovery metadata for one approved merchant after separate
approval. The preview must state that checkout, payment creation, live
payments, live Plural, direct provider credential handling, and direct provider
calls remain out of scope.

## Validation Gates Before Any Future Production Rollout

Every gate below must pass before any later AgenticOrg rollout proposal can
request public commerce discovery:

- Secret and private-detail scan of the public payload preview and related
  docs.
- Overclaim scan for readiness, certification, live payment, live Plural,
  checkout, production approval, provider approval, or broad Commerce V1 claims.
- Production-looking ID and name review to ensure only the approved real
  merchant identity appears and no synthetic IDs are present.
- Approved merchant allowlist review confirming AgenticOrg references only the
  human-approved Grantex merchant ID and never synthetic IDs.
- Grantex read-only smoke plan review confirming Grantex can run GET-only
  discovery checks, refusal checks, cache header checks, marker scans,
  rollback, and evidence retention.
- AgenticOrg dependency review confirming public commerce discovery remains
  gated until Grantex named merchant approval, Grantex read-only smoke
  evidence, and separate AgenticOrg approval exist.

## Stop Conditions

Stop the path and do not prepare an AgenticOrg rollout request if any condition
below is true:

- A named real Grantex merchant approval is missing.
- Legal, security, product, or ops approval is missing.
- Merchant owner approval is missing.
- Rollback owner, read-only smoke owner, support owner, incident owner, or
  evidence owner is missing.
- Any request requires broad `COMMERCE_V1_ENABLED`.
- Any request enables checkout or payment creation.
- Any request touches live payments or live Plural.
- Any provider credential would be exposed, logged, documented, or treated as
  public metadata.
- Any synthetic merchant ID, synthetic merchant name, or synthetic payload is
  proposed for production allowlist use.
- Any AgenticOrg public commerce discovery request appears before the Grantex
  named merchant approval, Grantex read-only smoke path, and separate
  AgenticOrg dependency approval exist.
- AgenticOrg would add a direct Stripe, Plural, Pine, or provider credential
  path instead of relying on Grantex-controlled metadata.

## Recommended Next Real Step

Collect the real human approval artifacts outside the repository. After the
approval packet exists, update intake documentation only with public-safe
summaries and non-secret private references. Do not include raw approval
messages, private merchant details, provider credentials, secret values, or
production config values in the repository.

## Explicit Non-Approval

- This plan does not approve a merchant.
- This plan does not approve production discovery.
- This plan does not approve a Grantex production allowlist value.
- This plan does not enable AgenticOrg public commerce discovery.
- This plan does not enable `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED`.
- This plan does not enable `COMMERCE_PUBLIC_DISCOVERY_ENABLED`.
- This plan does not set `COMMERCE_PUBLIC_DISCOVERY_MERCHANT_ALLOWLIST`.
- This plan does not enable Commerce V1.
- This plan does not enable checkout or payment creation.
- This plan does not enable live payments.
- This plan does not enable live Plural.
- This plan does not add direct provider handling to AgenticOrg.
- This plan does not treat synthetic smoke data as production approval.
