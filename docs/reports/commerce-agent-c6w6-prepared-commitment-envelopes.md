# Commerce Agent C6W6 - Prepared Commitment Envelopes

Status: implementation foundation, internal-only, non-enabling.

## Scope

C6W6 adds AgenticOrg-local prepared commitment request envelopes over C6W5 commitment-boundary decisions. Envelopes are buyer/seller handoff artifacts. They are not execution instructions and are not transaction authority.

This slice adds helper logic, tests, and internal documentation only. It does not add endpoints, migrations, workflows, provider adapters, public discovery, checkout/payment, live provider rail behavior, merchant private API behavior, allowlists, cloud, deploy, or external protocol publication behavior.

AgenticOrg remains the buyer/seller agent runtime. Grantex remains the trust, protocol, policy, and canonical-artifact authority. Merchant systems remain operational sources of record. Provider and fintech rails own payment and mandate execution.

## Envelope Kinds

AgenticOrg can prepare five internal envelope kinds:

- buyer_confirmation_request for local human confirmation prompts.
- seller_source_refresh_request for stale or missing source facts.
- merchant_confirmation_request for evidence-only merchant confirmation preparation.
- mandate_capability_evidence_request for cached mandate capability evidence preparation.
- support_escalation_preparation for non-binding support notes.

## Required Fields

Every envelope includes envelope_id, envelope_kind, created_at, expires_at, max_ttl_seconds, source_resolver_decision_id, action_class, requested_action, risk_tier, offline_mode_status, allowed_to_preview, allowed_to_prepare, allowed_to_execute, prepared_only, source artifact IDs and families, source authority, required fresh artifact families, freshness summary, blocked capabilities, unsupported capabilities, buyer-safe message, seller-safe message, next_human_step, next_system_step_label, redacted evidence refs, and non-authoritative transaction flags.

allowed_to_execute remains false. prepared_only remains true. next_system_step_label is a label, not an executable endpoint.

## Fail-Closed Rules

Envelope preparation fails closed when:

- the C6W5 resolver decision is missing.
- the resolver decision allows execution.
- source artifact IDs or families are absent.
- freshness or TTL metadata is missing, stale, expired, or ambiguous.
- the action is always blocked.
- the envelope kind does not match the requested action.
- commitment-bound amount, currency, or quantity context is ambiguous.
- the request implies live provider rails, payment, checkout, public discovery, merchant private API, shipping/carrier API, protocol publication, or certification-style claims.
- envelope content would include private credentials, raw JWTs, private URLs, raw provider payloads, DB/Redis URLs, private keys, or allowlist values.

## Confirmation Handoff

Buyer-safe messages tell the buyer that the request is prepared only. Seller-safe messages preserve source, freshness, unsupported capability, and non-authoritative wording. Source refresh and merchant confirmation envelopes ask for evidence only. Mandate capability evidence envelopes do not verify live mandates or call provider rails. Support escalation envelopes do not promise SLA, refund, return, replacement, settlement, or payout.

## Toll Booth Boundary

Grantex does not become a synchronous toll booth for non-binding agent interactions. AgenticOrg can continue non-binding and prepare-only flows from valid cached Grantex authority and C6W5 decisions while remaining fail-closed.

## What This Does Not Enable

C6W6 does not enable:

- Public discovery.
- Production Commerce V1.
- Checkout/payment creation.
- Payment capture or debit.
- Live payments.
- Live provider rail use.
- Provider calls.
- Carrier or shipping provider calls.
- Merchant private API calls.
- Connector credential export.
- Production allowlists.
- Public OACP publication.
- External protocol submission.
- Certification, compliance, conformance, standardization, production readiness, public-launch readiness, merchant approval, checkout approval, payment approval, live provider readiness, or OACP public readiness claims.

## Future Slices

Future slices must add separate controls before any execution handoff:

- Human confirmation audit records.
- Merchant-system source reconciliation.
- Provider-owned mandate verification outside C6W6.
- Explicit execution rails outside local preview and envelope helpers.
