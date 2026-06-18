# Commerce Agent C6W8 - Eligibility Packets

Status: implementation foundation, internal-only, non-enabling.

## Scope

C6W8 adds AgenticOrg-local controlled execution handoff eligibility packets and audit trail preparation over C6W7 reconciliation results. Packets answer whether a prepared and reconciled request has enough cached evidence to be reviewed by a future controller slice. They are not execution instructions and are not transaction authority.

This slice adds helper logic, tests, and internal documentation only. It does not add endpoints, migrations, workflows, provider adapters, production audit persistence, public discovery, checkout/payment, live provider rail behavior, merchant private API behavior, carrier or shipping behavior, allowlists, cloud, deploy, or external protocol publication behavior.

AgenticOrg remains the buyer/seller agent runtime. Grantex remains the trust, protocol, policy, and canonical-artifact authority. Merchant systems remain operational sources of record. Provider and fintech rails own payment and mandate execution.

## Packet Kinds

AgenticOrg can prepare five local packet kinds:

- execution_handoff_eligibility_packet for future controller review eligibility.
- audit_trail_preparation_packet for redacted evidence lineage without production audit writes.
- missing_evidence_packet for missing source artifacts, confirmations, or freshness requirements.
- blocked_execution_packet for buyer/seller-safe refusal and blocked handoff messaging.
- manual_review_packet for human review labels without approving merchant, payment, provider rail, shipping, or live execution.

## Eligibility Statuses

C6W8 uses a small fail-closed status enum:

- eligible_for_future_handoff
- missing_evidence
- needs_human_review
- blocked
- stale
- expired
- mismatched
- unsupported

eligible_for_future_handoff means cached evidence may be reviewed by a future controlled handoff slice. It does not mean checkout, payment, order, hold, refund, return, shipment, provider rail use, mandate creation, merchant private API execution, public discovery, or production operation.

## Required Fields

Every packet includes packet_id, packet_kind, created_at, expires_at, max_ttl_seconds, reconciliation_id, envelope_id, response_kind, response_status, requested_action, action_class, risk_tier, eligibility_status, eligibility_reason, missing_requirements, required_confirmations, source artifact IDs and families, response evidence refs, audit lineage refs, freshness summary, unsupported capabilities, blocked capabilities, buyer-safe message, seller-safe message, next_human_step, next_system_step_label, and non-authoritative transaction flags.

allowed_to_execute remains false. prepared_only remains true. reconciled_only remains true. eligibility_only remains true. next_system_step_label is a label, not an executable endpoint.

## Fail-Closed Rules

Eligibility packet preparation fails closed or returns an ineligible packet when:

- the C6W7 reconciliation is missing.
- the reconciliation allows execution.
- the reconciliation is not prepared_only or not reconciled_only.
- the reconciliation status is not accepted_for_preparation.
- source artifact IDs, families, freshness, or TTL metadata is missing, stale, expired, or ambiguous.
- response evidence refs or audit lineage refs are missing, private, raw, or unredacted.
- required human, source, merchant, or mandate confirmations are missing.
- commitment-bound amount, currency, or quantity context is ambiguous.
- mandate capability evidence is missing or older than the mandate TTL at the commitment boundary.
- packet flags imply checkout, payment, order, hold, refund, return, shipping, provider call, carrier call, merchant private API use, public discovery enablement, protocol publication/submission, certification, or production readiness.
- packet content would expose private credentials, raw JWTs, private URLs, raw provider payloads, private customer data, DB/Redis URLs, private keys, or allowlist values.

## Evidence Lineage

C6W8 packets carry redacted lineage from the C6W7 reconciliation, C6W6 envelope, C6W5 decision, C6W4 adapter preview, and C6W3 signed artifact families. AgenticOrg preserves source artifact IDs, families, freshness, risk, unsupported capabilities, blocked capabilities, and evidence refs without inventing commerce facts.

Missing evidence packets route to source refresh labels. Manual review packets route to human review labels. Blocked packets keep buyer/seller-safe refusal wording.

## Eligibility Is Not Execution

Eligibility means a future controlled handoff slice can review the packet. It does not execute or authorize the requested action. C6W8 never creates orders, holds, checkout sessions, payment intents, mandates, refunds, returns, shipments, support promises, or provider rail operations.

## Toll Booth Boundary

Grantex remains the canonical artifact and policy authority without becoming a synchronous toll booth for non-binding agent interactions. AgenticOrg prepares local packets from cached evidence and preserves merchant/provider operational authority for any future execution work.

## What This Does Not Enable

C6W8 does not enable:

- Public discovery.
- Production Commerce V1.
- Checkout/payment creation.
- Order creation.
- Inventory holds.
- Payment capture or debit.
- Mandate creation.
- Refund or return execution.
- Shipping or carrier calls.
- Live provider rail use.
- Provider calls.
- Merchant private API calls.
- Connector credential export.
- Production allowlists.
- Production audit persistence changes.
- Public OACP publication.
- External protocol submission.
- Certification, compliance, conformance, standardization, production readiness, public-launch readiness, merchant approval, checkout approval, payment approval, live provider readiness, execution readiness, or OACP public readiness claims.

## Future Slices

Future slices must still add separate controls before any real execution:

- Production audit storage with privacy controls.
- Source-owned merchant confirmation ingestion.
- Provider-owned mandate verification outside local packet helpers.
- Explicit execution-controller authorization boundaries.
- Runtime integration tests proving provider, merchant, shipping, and payment systems remain external operational authorities.
