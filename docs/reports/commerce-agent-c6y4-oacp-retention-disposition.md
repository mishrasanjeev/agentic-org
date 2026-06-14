# C6Y4 OACP Retention Disposition Dry-Run

## Scope

C6Y4 adds internal helper behavior that consumes C6Y3 redacted audit review manifest summaries and produces retention disposition dry-runs plus operator review packets. AgenticOrg owns audit review manifest handling and retention review previews for its local durable OACP metadata.

The slice is planning and model/helper behavior only. It does not expose an endpoint, command, CLI, workflow, scheduler, cron job, queue, background worker, migration, export-file writer, or generated report artifact.

## Correct Ownership Model

AgenticOrg remains the buyer and seller AI-agent runtime. It owns durable/local OACP artifact cache behavior, local operator decision handling, audit export bundle generation, audit review manifest handling, internal manifest query/summary behavior, and C6Y4 retention disposition dry-runs.

Grantex remains the trust, protocol, policy, and canonical OACP artifact authority. Grantex is not a transaction toll booth and does not receive every manifest summary, retention disposition dry-run, operator packet, or non-binding buyer/seller interaction.

Merchant systems remain operational sources of record. Provider and fintech rails own mandate and payment execution. OACP remains internal, non-publication, non-certifying, non-production, and non-executing.

## Disposition Dry-Run Outcomes

C6Y4 produces deterministic disposition previews only:

- retain
- review_later
- legal_hold_review
- redaction_review_required
- retention_due_review
- blocked_unsafe

The preview can indicate that an operator should review a due retention boundary, legal hold candidate, missing redacted-evidence count, empty scope result, or unsafe input. It cannot delete, purge, retain, export, approve, or execute anything.

## Operator Review Packet

The operator review packet carries label-only disposition previews from the dry-run. It retains tenant, merchant, seller-agent, buyer-agent, retention, risk, freshness, revocation, blocked, unsupported, and evidence-count summaries.

The packet includes:

- packet id and generated_at
- source C6Y3 summary id
- source C6Y4 dry-run id
- scope summary
- manifest_count
- retention_due_count
- legal_hold_candidate_count
- artifact and risk counts
- blocked and unsupported capability summaries
- redacted evidence ref count only
- next-step labels only
- allowed_to_execute = false
- future_retention_action_allowed = false
- records_deleted = false
- non_authoritative_for_transaction = true

## Fail-Closed Rules

C6Y4 fails closed when the C6Y3 summary is missing, malformed, executable, missing tenant or merchant scope, has mismatched scope, includes evidence ref values instead of counts, contains malformed timestamps, has export-writer/scheduler/CLI/migration flags, has false non-enablement flags, includes private/raw values, or contains publication, approval, certification, compliance, conformance, standardization, or readiness wording.

Operator packets fail closed unless the dry-run is safe, ready for operator review, label-only, non-executing, and explicitly blocks future retention action.

## Migration Scheduler CLI And Export Writer Decision

C6Y4 adds no DB migration. It reuses C6Y3 redacted summary structures and C6Y2 durable manifest metadata.

C6Y4 adds no scheduler, cron job, queue, background worker, CLI, export-file writer, generated report artifact, endpoint, public API, or runtime OpenAPI contract. It does not write export files and does not call Grantex live, providers, merchant systems, carriers, shipping providers, or payment rails.

## Guardrails

The dry-run and operator packet keep the non-enablement posture intact:

- allowed_to_execute = false
- future_retention_action_allowed = false
- records_deleted = false
- retention_executed = false
- non_authoritative_for_transaction = true
- no_checkout_payment_enablement = true
- no_live_provider_enablement = true
- no_public_discovery_enablement = true
- no_export_file_written = true

Outputs must never include raw artifact payloads, provider payloads, connector payloads, raw JWTs or passports, credentials, tokens, private keys, private customer data, payment data, bank/card data, raw merchant private API values, raw reviewer identity, or secrets.

## What C6Y4 Does Not Enable

C6Y4 does not execute retention, delete records, purge records, write export files, schedule retention, expose APIs, create orders, create holds, capture payments, create mandates, issue refunds, process returns, create shipments, call providers, call carriers, call shipping providers, call Grantex live, call merchant private APIs, publish OACP, submit protocols externally, or enable public discovery.

C6Y4 dry-runs and operator packets are not Grantex approvals, merchant approvals, checkout approvals, payment approvals, mandate approvals, live-provider approvals, production approvals, public launch approvals, certifications, compliance statements, conformance statements, standardization claims, or OACP approvals.

## Future Work

A future approved slice may add durable disposition-decision records or an internal operator surface. That work must remain tenant-scoped, redacted, non-executing, and separately reviewed before any endpoint, scheduler, CLI, export writer, migration, or production retention action is introduced.
