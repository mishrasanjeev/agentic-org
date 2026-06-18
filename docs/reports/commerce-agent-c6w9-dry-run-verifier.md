# Commerce Agent C6W9 Dry-Run Verifier

## Scope

C6W9 adds an internal AgenticOrg dry-run verifier over C6W8 eligibility packets. The verifier checks whether a local packet has enough lineage, freshness, evidence refs, confirmations, risk context, and non-enablement flags for a future gated controller review.

The verifier produces local dry-run results only. It does not create an execution controller, execute a transaction, call Grantex live, call providers, call merchant private APIs, create checkout or payment objects, enable public discovery, or persist production audit events.

## Dry-Run Result Kinds

- `execution_controller_handoff_dry_run`
- `audit_readiness_verification`
- `missing_contract_requirement`
- `blocked_handoff_verification`
- `manual_review_required_verification`

Each result kind is internal and non-executing. AgenticOrg can use the result to explain what is complete, missing, blocked, or routed to human review.

## Verifier Statuses

- `dry_run_accepted_for_future_controller`
- `missing_contract_requirement`
- `needs_human_review`
- `blocked`
- `stale`
- `expired`
- `mismatched`
- `unsupported`
- `unsafe`

Only `dry_run_accepted_for_future_controller` means the local packet contract shape is complete enough for a future controller review. It is still not execution readiness and does not approve live behavior.

## Required Fields

Verifier results include:

- deterministic `verification_id`
- `verification_kind` and `verification_status`
- `created_at`, `expires_at`, and `max_ttl_seconds`
- `eligibility_packet_id`, `packet_kind`, and `eligibility_status`
- `reconciliation_id` and `envelope_id`
- `requested_action`, `action_class`, and `risk_tier`
- source artifact IDs and families
- redacted response evidence refs and audit lineage refs
- required confirmations and missing requirements
- freshness summary
- contract checks and audit-readiness checks
- unsupported and blocked capabilities
- buyer, seller, and operator-safe messages
- label-only next human and next system steps
- `allowed_to_execute remains false`
- `dry_run_only remains true`
- `eligibility_only remains true`
- `non_authoritative_for_transaction remains true`
- no checkout/payment, live-provider, or public-discovery enablement

AgenticOrg does not invent commerce facts from packet data. The verifier preserves source and blocked wording from the packet.

## Contract Checks

The runtime verifier checks:

- packet kind recognition
- eligibility status acceptability for the requested verifier kind
- reconciliation and envelope lineage
- source artifact refs
- redacted, non-private evidence refs
- required confirmations
- freshness and TTL
- mandate evidence freshness when relevant
- action class and risk tier consistency
- amount, currency, and quantity context for commitment-bound actions
- non-enablement flags
- absence of executable URLs, endpoint targets, provider targets, merchant private targets, or live rail targets
- absence of raw private labels or payloads
- absence of publication, submission, certification, compliance, conformance, production-readiness, or execution-readiness claims

## Audit Readiness

Audit-readiness verification confirms that the local packet carries:

- audit lineage refs
- redacted audit refs
- reconciliation and envelope lineage
- source refs
- response evidence refs
- buyer, seller, and operator-safe messages

C6W9 does not persist production audit records. It prepares local audit-readiness facts for a later gated slice.

## Fail-Closed Rules

The verifier blocks or marks unsafe when:

- the C6W8 packet is missing
- the packet allows execution
- the packet is not prepared-only, reconciled-only, and eligibility-only
- the eligibility status does not match the verifier kind
- lineage is missing or mismatched
- evidence refs are missing, raw, private, or unredacted
- required confirmations are missing
- freshness or TTL is stale or expired
- risk context is missing or inconsistent
- mandate evidence is stale or missing at the boundary
- non-enablement flags are missing or false
- executable URLs, endpoint targets, provider targets, merchant private targets, or live rail targets appear
- the packet implies checkout, payment, order, hold, refund, return, shipping, provider execution, public discovery enablement, publication, or readiness claims
- secrets, raw JWTs, private keys, raw provider payloads, private merchant URLs, DB or Redis URLs, private customer data, or production allowlist values appear

## Dry-Run Acceptance Is Not Execution

Dry-run acceptance is a local contract check. It is not execution readiness, merchant approval, checkout approval, payment approval, live-provider readiness, public-launch readiness, certification, compliance, conformance, or standardization.

## Toll Booth Boundary

AgenticOrg can continue non-binding and prepare-only flows from valid cached artifacts without routing every interaction through Grantex. Grantex remains the artifact and policy authority; AgenticOrg remains the agent runtime. Merchant systems and provider rails remain the operational execution authorities.

## What This Does Not Enable

C6W9 does not enable:

- public discovery
- checkout or payment
- live providers or live rails
- live Plural behavior
- production Commerce V1
- merchant private API calls
- provider, carrier, or shipping calls
- production audit persistence
- protocol publication or submission
- certification, compliance, conformance, standardization, production-readiness, or execution-readiness claims

## Future Slices

Future slices would need separately gated execution-controller ownership, merchant-system handoff contracts, provider-owned payment and mandate behavior, production audit persistence, human approval flows, rollback handling, and operational controls. Those are outside C6W9.
