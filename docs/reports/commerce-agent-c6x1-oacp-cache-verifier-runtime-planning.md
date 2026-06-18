# Commerce Agent C6X1 OACP Cache and Verifier Runtime Planning

## Scope

C6X1 is an internal planning and test slice for an AgenticOrg persistent OACP artifact cache and Grantex artifact issuance/verifier runtime boundaries. It defines the local runtime contract AgenticOrg should eventually consume, but it does not add runtime execution behavior.

AgenticOrg remains the buyer and seller AI-agent runtime. Grantex remains the trust, protocol, policy, and canonical OACP artifact authority. Merchant systems remain operational sources of record. Provider and fintech rails own mandate and payment execution.

## Correct Ownership Model

- AgenticOrg owns buyer/seller agent behavior, cache consumption, channel-safe messages, source-aware labels, and fail-closed decisions.
- Grantex owns canonical OACP artifact schemas, issuance policy, verifier policy, revocation posture, and artifact lineage rules.
- Merchant systems own operational facts such as catalog, inventory, fulfillment, order state, support state, return state, and refund state.
- Provider and fintech rails own mandate capability, mandate setup, payment authorization, capture, settlement, payout, and provider-owned evidence.
- AgenticOrg seller agents may initiate approved connector sync jobs in a later approved slice, but C6X1 does not add those jobs.
- AgenticOrg may verify provider-owned mandate capability directly where separately approved, but C6X1 does not add live verification calls.
- Valid cached OACP artifacts may support non-binding interactions without routing every buyer or seller turn through Grantex.

## Persistent Cache Model

The future AgenticOrg cache should store only the local metadata required to evaluate whether cached OACP artifacts can be used for preview, preparation, or future handoff checks:

- artifact envelope and canonical artifact ID
- artifact family and schema version
- source authority and source family
- issued-at, received-at, and expires-at timestamps
- TTL policy and freshness status
- revocation snapshot reference and age
- signature or verifier result reference
- non-sensitive evidence references
- blocked capability wording
- unsupported capability wording
- non-enablement flags
- last local validation result

The cache must not store raw credentials, raw JWTs, raw provider payloads, private merchant API URLs, DB or Redis URLs, private keys, production allowlist values, or private customer data.

## Grantex Issuance And Verifier Boundary

AgenticOrg should treat Grantex as the authority for canonical OACP artifact issuance and verifier policy, not as a live transaction broker. A future Grantex issuer/verifier contract should provide signed artifact shape, TTL, freshness, revocation, policy posture, source lineage, and evidence-reference validity.

AgenticOrg must not treat adapter previews, seller cards, cached summaries, or user text as transaction authority. Adapter previews cannot override expired, revoked, stale, missing, or unsupported canonical artifacts.

## Non-Binding Cache Use

Valid cached artifacts may support non-binding agent behavior:

- browse merchant profile
- inspect seller card
- compare catalog summaries
- explain policy
- explain available capabilities
- show source and freshness labels
- prepare buyer questions
- prepare seller-agent remediation suggestions

These flows may continue from cache when Grantex is unavailable and local freshness and revocation posture remain valid. They must stay source-aware and non-authoritative for transactions.

## Commitment-Bound Use

Commitment-adjacent and commitment-bound actions must use the C6W5 through C6W9 fail-closed chain:

- commitment boundary resolver
- prepared request envelope
- response reconciliation
- eligibility packet
- dry-run verifier

C6X1 does not execute orders, holds, payments, mandates, refunds, returns, shipments, provider calls, merchant private API calls, public discovery changes, or production audit persistence.

## Freshness, Revocation, And TTL Defaults

The planning cache keeps the established TTL posture unless a later approved slice changes it:

| Artifact family | Planning TTL posture |
| --- | --- |
| merchant capability | 24h |
| seller agent capability | 6h |
| policy | 6h |
| catalog | 6h |
| offer | 15m |
| price | 5m, or 60s for dynamic price |
| inventory | 60s, or 30s for high-velocity goods |
| public discovery display facts | 15m display/read only |
| mandate capability | 2m at commitment boundary |
| protocol adapter | 24h max and never longer than referenced artifacts |

Missing, stale, or ambiguous revocation posture is fail-closed. Cached artifacts must expire locally even when the upstream source is unreachable.

## Evidence References

AgenticOrg should preserve non-sensitive evidence references from Grantex artifacts and local response/reconciliation results. Evidence references are lineage pointers. They are not provider payloads, merchant private API payloads, payment instructions, mandate instructions, or customer data stores.

When evidence is required but missing, stale, private, or unredacted, AgenticOrg should return a buyer/seller-safe refusal or source-refresh request.

## Fail-Closed Rules

AgenticOrg cache planning fails closed when:

- artifact envelope is missing
- artifact ID, family, source authority, issued-at, expires-at, TTL, freshness, or revocation posture is missing
- signature or verifier result is missing or invalid
- artifact is stale, expired, revoked, ambiguous, mismatched, superseded, or unsupported
- evidence refs are missing for commitment-bound checks
- evidence refs are raw, private, or unredacted
- adapter preview tries to override missing, expired, or revoked canonical artifacts
- amount, currency, quantity, price, inventory, mandate, policy, or risk context is ambiguous
- any field implies checkout, payment, live provider rails, public discovery enablement, merchant private API calls, protocol publication, certification, conformance, production launch, or execution approval

## Guardrails

C6X1 guardrails require:

- docs/tests/planning-first only
- no runtime code
- no public endpoint
- no public OACP publication
- no checkout or payment enablement
- no live provider rail enablement
- no merchant private API execution
- no production config change
- no production allowlist assignment
- no migration
- no workflow
- no public docs navigation entry
- no landing page runtime UI
- no blog post publication
- no certification, compliance, conformance, standardization, production-readiness, public-launch-readiness, merchant-approval, checkout-approval, payment-approval, live-provider-readiness, or execution-readiness claim

## What This Does Not Enable

C6X1 does not enable public discovery, production Commerce V1, checkout, payment, live provider rails, live mandate behavior, merchant private APIs, carrier or shipping calls, production audit persistence, OACP publication, or any execution controller.

## Future Work

Future slices would need separate approval for persistent cache implementation, cache invalidation, local verifier result storage, Grantex issuer/verifier plumbing, revocation snapshot distribution, signature verification, connector sync ownership, production audit persistence, and controlled execution handoff ownership.

Those future slices must keep non-binding cached interactions from requiring Grantex on every turn and must preserve merchant and provider execution ownership.
