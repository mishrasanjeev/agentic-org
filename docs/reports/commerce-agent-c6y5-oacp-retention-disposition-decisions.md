# C6Y5 OACP Retention Disposition Decisions

## Scope

C6Y5 adds durable AgenticOrg retention disposition decision records over C6Y4 retention disposition dry-runs and operator review packets. The slice stores redacted, audit-safe decision metadata only.

This does not execute retention, delete records, purge records, redact persisted records, write export files, schedule jobs, call Grantex live, call providers, call merchant systems, expose APIs, or publish OACP.

## Correct Ownership Model

AgenticOrg is the buyer and seller AI-agent runtime. AgenticOrg owns durable/local OACP artifact cache behavior, local operator decision handling, audit export bundle generation, audit review manifest handling, manifest query/summary behavior, and retention disposition review.

AgenticOrg owns retention disposition review and stores these disposition decisions locally for internal operator review.

Grantex remains the trust, protocol, policy, and canonical OACP artifact authority. Grantex does not become a transaction toll booth for non-binding buyer/seller agent interactions, audit review summaries, retention disposition dry-runs, operator packets, or disposition decisions.

Merchant systems remain operational sources of record. Provider and fintech rails own mandate and payment execution where separately approved.

## Persistence Decision

C6Y5 implements persistence because AgenticOrg already has the same safe pattern for internal OACP records:

- C6X4 durable artifact cache records
- C6X8 durable operator decision records
- C6Y2 durable audit review manifest records

The new Alembic migration is `v6y5_retention_decisions` and depends on `v6y3_industry_pack_uuid_default`, the current migration head in this branch. It adds one narrow table, tenant-safe indexes, a uniqueness guard for packet/kind/reviewer scope, rollback, and RLS.

## Durable Repository Contract

The C6Y5 repository methods are:

- `upsert_disposition_decision`
- `get_disposition_decision`
- `list_disposition_decisions_for_scope`
- `evaluate_disposition_decision_for_future_review`

Evaluation is non-executing. It can say that future retention review needs a separate action, but it cannot delete, purge, redact, export, schedule, call Grantex, call providers, or call merchant private APIs.

## Stored Fields

C6Y5 stores redacted decision metadata:

- disposition_decision_id
- source_summary_id
- source_dry_run_id
- source_operator_packet_id
- tenant_id and merchant_id
- seller_agent_id and buyer_agent_id when applicable
- generated_at, decided_at, retention_class, and retain_until
- decision_kind
- manifest_count, retention_due_count, and legal_hold_candidate_count
- artifact_family_counts and risk_tier_counts
- blocked and unsupported capability summaries
- redacted_evidence_ref_count only
- redacted reason codes
- opaque reviewer_ref only
- next_step_labels
- non-enablement flags
- allowed_to_execute = false
- future_retention_action_allowed = false
- records_deleted = false
- retention_executed = false
- non_authoritative_for_transaction = true

Decision kinds are limited to:

- approve_future_retention_review
- approve_future_redaction_review
- approve_future_legal_hold_review
- request_more_evidence
- reject_disposition
- defer_until_recheck
- block_unsafe_disposition

The `approve_future_*` labels are review labels only. They are not merchant, Grantex, checkout, payment, mandate, live-provider, production, certification, compliance, conformance, standardization, or public launch decisions.

## Fail-Closed Persistence And Evaluation

C6Y5 refuses storage when:

- disposition_decision_id, source_summary_id, source_dry_run_id, source_operator_packet_id, tenant_id, or merchant_id is missing
- decision_kind or retention_class is not supported
- generated_at, decided_at, or retain_until is malformed or impossible
- dry-run and operator-packet scope does not match
- raw or private refs appear
- reviewer_ref is a raw email, phone, token, credential, or non-opaque value
- executable or action-target fields appear
- export writer, scheduler, worker, queue, CLI, deletion, purge, or retention execution flags appear
- non-enablement flags are false
- public discovery, payment, live rail, provider, private API, publication, approval, or readiness wording appears outside negative guardrail wording

Failed evaluations return blocked, non-executing records with `allowed_to_execute = false`, `future_retention_action_allowed = false`, `records_deleted = false`, and `retention_executed = false`.

## Migration Safety

The migration is narrow and tenant safe:

- one new internal table
- no runtime schema mutation
- no production config
- no public endpoint
- tenant_id and merchant_id are required
- seller_agent_id and buyer_agent_id are indexed when present
- source_summary_id, source_dry_run_id, source_operator_packet_id, decision_kind, retention_class, retain_until, and decided_at are indexed
- unique guard prevents duplicate active decision records for the same tenant/merchant/seller/buyer/operator packet/decision kind/reviewer
- RLS is enabled and forced with `agenticorg.tenant_id`
- rollback drops the RLS policy, indexes, and table

The table stores no source-of-record merchant data, provider data, payment data, card data, bank data, raw customer data, raw artifact payloads, raw connector payloads, or credentials.

## Guardrails

C6Y5 remains:

- internal-only
- non-publication
- non-certifying
- non-production
- non-executing
- fail-closed
- export-writer-free
- scheduler-free
- CLI-free
- endpoint-free
- non-authoritative for transactions

It keeps:

- allowed_to_execute = false
- future_retention_action_allowed = false
- records_deleted = false
- retention_executed = false
- no_checkout_payment_enablement = true
- no_live_provider_enablement = true
- no_public_discovery_enablement = true

## What C6Y5 Does Not Enable

C6Y5 does not execute retention, delete records, purge records, redact persisted records, write export files, create jobs, schedule jobs, expose endpoints, add public OpenAPI runtime contracts, create orders, create holds, capture payments, create mandates, issue refunds, process returns, create shipments, call providers, call carriers, call shipping providers, call merchant private APIs, call Grantex live endpoints, publish OACP, submit protocols externally, or enable public discovery.

C6Y5 does not claim certification, compliance, conformance, standardization, production readiness, public launch readiness, merchant approval, checkout approval, payment approval, mandate approval, live-provider readiness, or OACP approval.

## Future Work

A future approved slice may add an internal operator UI or a separate retention execution controller. That future work must remain separately reviewed and must not infer execution authority from C6Y5 decision records.
