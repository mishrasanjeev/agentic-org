# Commerce Agent C6W7 - Response Reconciliation

Status: implementation foundation, internal-only, non-enabling.

## Scope

C6W7 adds AgenticOrg-local prepared commitment response intake and evidence reconciliation over C6W6 prepared envelopes. Reconciliation results are local evidence records. They are not execution instructions and are not transaction authority.

This slice adds helper logic, tests, and internal documentation only. It does not add endpoints, migrations, workflows, provider adapters, public discovery, checkout/payment, live provider rail behavior, merchant private API behavior, carrier or shipping behavior, allowlists, cloud, deploy, or external protocol publication behavior.

AgenticOrg remains the buyer/seller agent runtime. Grantex remains the trust, protocol, policy, and canonical-artifact authority. Merchant systems remain operational sources of record. Provider and fintech rails own payment and mandate execution.

## Response Evidence Kinds

AgenticOrg can reconcile five internal response evidence kinds:

- buyer_confirmation_response for local buyer confirmation, rejection, clarification, or refresh requests.
- seller_source_refresh_response for refreshed, unchanged, missing, stale, or ambiguous seller/source facts.
- merchant_confirmation_response for merchant or source-owner confirm, reject, expire, or manual-review responses.
- mandate_capability_evidence_response for cached provider-owned mandate capability evidence states.
- support_escalation_response for support acknowledgment, rejection, or manual-review states.

## Reconciliation Output

Every reconciliation includes reconciliation_id, envelope_id, envelope_kind, response_kind, response_status, created_at, expires_at, max_ttl_seconds, action_class, requested_action, risk_tier, source artifact IDs and families, source authority, response evidence refs, freshness summary, decision summary, unsupported capabilities, blocked capabilities, required next artifact families, buyer-safe message, seller-safe message, next_human_step, next_system_step_label, and non-authoritative transaction flags.

allowed_to_execute remains false. prepared_only remains true. reconciled_only remains true. next_system_step_label is a label, not an executable endpoint.

## Status Enum

C6W7 uses a small fail-closed status enum:

- accepted_for_preparation
- rejected
- needs_source_refresh
- needs_human_review
- expired
- stale
- mismatched
- blocked

accepted_for_preparation means cached evidence may continue as preparation only. It does not mean checkout, payment, order, hold, refund, return, shipment, provider rail use, mandate creation, or merchant private API execution.

## Fail-Closed Rules

Response reconciliation fails closed when:

- the C6W6 envelope is missing.
- the envelope allows execution.
- the envelope is not prepared_only.
- source artifact IDs, families, freshness, or TTL metadata is missing, stale, expired, or ambiguous.
- response kind does not match envelope kind.
- response status or flags try to execute or approve live action.
- response evidence refs are missing.
- response evidence contains private credentials, raw JWTs, private URLs, raw provider payloads, private customer data, DB/Redis URLs, private keys, or allowlist values.
- response evidence indicates checkout, payment, order, hold, refund, return, shipping, provider call, carrier call, merchant private API use, public discovery enablement, protocol publication/submission, certification, or production readiness.
- commitment-bound amount, currency, or quantity context is ambiguous.
- mandate capability evidence is older than the mandate TTL at the commitment boundary.
- merchant or source responses conflict with the C6W5 decision or C6W6 envelope metadata.

## Human And Source Responses

Buyer-safe messages tell the buyer that response intake is reconciled locally and remains prepared-only. Seller-safe messages preserve source, freshness, unsupported capability, blocked capability, and non-authoritative wording. Seller refresh responses can carry new artifact IDs only as evidence references. Merchant confirmation responses do not create orders, holds, payments, refunds, returns, or shipments. Mandate evidence responses do not verify live mandates or call provider rails. Support responses do not promise SLA, refund, return, replacement, settlement, or payout.

## Toll Booth Boundary

Grantex does not become a synchronous toll booth for non-binding agent interactions. AgenticOrg can reconcile cached responses locally from C6W6 envelopes and Grantex-authoritative metadata while preserving source authority and remaining fail-closed.

## What This Does Not Enable

C6W7 does not enable:

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
- Public OACP publication.
- External protocol submission.
- Certification, compliance, conformance, standardization, production readiness, public-launch readiness, merchant approval, checkout approval, payment approval, live provider readiness, or OACP public readiness claims.

## Future Slices

Future slices must add separate controls before any execution handoff:

- Human confirmation audit storage.
- Merchant-system source response ingestion from source-owned channels.
- Provider-owned mandate verification outside C6W7.
- Explicit controlled execution handoff outside local preview and reconciliation helpers.
