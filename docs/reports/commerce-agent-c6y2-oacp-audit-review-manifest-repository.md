# Commerce Agent C6Y2 OACP Audit Review Manifest Repository

## Scope

C6Y2 adds an internal AgenticOrg durable repository for C6Y1 OACP audit export review manifests and retention boundary metadata. The repository stores redacted, audit-safe manifest metadata only. It does not write export files, expose an API, run jobs, schedule maintenance, call Grantex live, call providers, call merchant systems, refresh artifacts, execute cache maintenance, or execute commerce actions.

## Correct Ownership Model

AgenticOrg is the buyer and seller AI-agent runtime. AgenticOrg owns durable and local OACP artifact cache behavior, local operator decision handling, internal audit export bundle generation, and audit review manifest handling. AgenticOrg owns audit review manifest handling inside the local runtime boundary. Grantex remains the trust, protocol, policy, and canonical OACP artifact authority. Grantex is not a transaction toll booth for every non-binding buyer or seller agent turn. Merchant systems remain operational sources of record, and provider or fintech rails own mandate and payment execution where separately approved.

## Persistence Decision

C6Y2 implements persistence because the existing C6X4 and C6X8 AgenticOrg migrations establish a narrow Alembic-managed pattern for OACP-owned durable internal records with tenant indexes, non-enablement constraints, rollback, and RLS. The migration is `v6y2_oacp_review_manifests` and depends on `v6x9_audit_log_action_text`, the current migration head.

The repository is internal only. It stores review metadata after C6Y1 manifest generation and does not create a command, CLI, scheduler, worker, queue, route, public endpoint, or public OpenAPI runtime contract.

## Durable Repository Contract

The repository methods are:

- `upsert_manifest`
- `get_manifest`
- `list_manifests_for_scope`
- `evaluate_manifest_for_internal_review`

Evaluation is review-only. It returns `future_export_allowed = false`, `allowed_to_execute = false`, `export_file_written = false`, `export_writer_added = false`, and `non_authoritative_for_transaction = true`.

## Stored Fields

The table preserves manifest id, bundle id, tenant id, merchant id, seller-agent id, buyer-agent id, generated timestamps, bundle generated timestamp, retention class, retention days, retain-until timestamp, retention clock source, artifact family counts, cache record references, maintenance plan references, review packet references, decision record references, redacted reason codes, redacted source refs, redacted evidence refs, freshness and TTL summary, revocation snapshot summary, risk-tier summary, unsupported capability summary, blocked capability summary, next-step labels, non-enablement flags, and created or updated timestamps.

The repository stores only redacted refs and summary metadata. It does not store raw artifact payloads, provider payloads, connector payloads, raw JWTs or passports, credentials, tokens, private keys, private customer data, payment data, bank or card data, raw merchant private API values, raw reviewer identity values, secrets, production allowlists, executable URLs, or action targets.

## Retention Boundary

C6Y2 persists C6Y1 internal retention classes only: `short_lived_internal_review`, `standard_internal_review`, and `legal_hold_candidate`. The retention window is metadata for review and retention boundary handling. It is not public publication, public launch readiness, checkout approval, payment approval, mandate approval, live-provider approval, merchant approval, certification, compliance, conformance, or standardization.

## Fail-Closed Persistence And Evaluation

The repository refuses storage when manifest id, bundle id, tenant id, or merchant id is missing; the manifest is not a C6Y1 ready review manifest; retention class is invalid; generated, bundle generated, or retain-until timestamps are malformed or unordered; direct scope and summary scope mismatch; private or raw refs are present; executable or export-writer flags are present; non-enablement flags are false; next-step labels imply execution; or publication, approval, or readiness wording appears.

Blocked records are not stored. Missing records evaluate to blocked. Stored records evaluate as internal review records only and do not authorize future export, cache maintenance, checkout, payment, provider, merchant private API, public discovery, or Grantex live behavior.

## Migration Safety

The migration adds one narrow table, tenant and scope indexes, a uniqueness guard for bundle and retention scope, timestamp ordering checks, non-execution constraints, and tenant RLS using `agenticorg.tenant_id`. Rollback drops the policy, indexes, unique scope guard, and table. There is no runtime startup schema mutation and no production config change.

## Guardrails

C6Y2 remains internal-only, non-publication, non-certifying, non-production, non-executing, fail-closed, scheduler-free, export-writer-free, and non-authoritative for transactions. It keeps `allowed_to_execute = false`, `future_export_allowed = false`, `non_authoritative_for_transaction = true`, `no_checkout_payment_enablement = true`, `no_live_provider_enablement = true`, and `no_public_discovery_enablement = true`.

## What C6Y2 Does Not Enable

C6Y2 adds no public endpoint, route, public OpenAPI runtime contract, workflow, scheduler, cron job, queue, background worker, CLI, export-file writer, production config, secret, public discovery enablement, checkout, payment, order, hold, refund, return, shipping execution, live provider rail, live Plural behavior, provider call, merchant private API call, allowlist, external OACP publication, or approval/readiness claim.

Grantex does not receive every review manifest, audit bundle, cache report, operator decision, or non-binding buyer or seller interaction.

## Future Work

Future slices may define a controlled internal review surface, explicit retention operations, or an export writer. Those slices must remain separate from checkout, payment, provider rails, merchant private APIs, public OACP publication, production config, workflow scheduling, public endpoint behavior, and readiness or approval claims unless separately approved.
