# Commerce Agent C6X8 OACP Operator Decision Repository

## Scope

C6X8 adds a durable AgenticOrg repository for audit-safe C6X7 operator decision records over local OACP cache maintenance review packets. The repository stores redacted decision metadata only. It does not call Grantex live, refresh artifacts, evict records, quarantine records, schedule maintenance, call merchant systems, call providers, or expose an API.

## Correct Ownership Model

AgenticOrg is the buyer and seller AI-agent runtime. AgenticOrg owns local operator decision handling for its durable OACP artifact cache. Grantex remains the trust, protocol, policy, and canonical OACP artifact authority. Grantex is not a transaction toll booth for every non-binding buyer or seller agent turn. Merchant systems remain operational sources of record, and provider or fintech rails own mandate and payment execution where separately approved.

## Persistence Decision

C6X8 implements persistence because C6X4 established a safe Alembic and SQLAlchemy pattern for internal OACP cache storage. The new repository mirrors that pattern with a narrow table, tenant-safe indexes, row-level security, static migration tests, rollback notes, and non-execution constraints.

The migration is `v6x8_oacp_operator_decisions`. It creates only `oacp_operator_decision_records` and does not mutate runtime startup schema, production config, workflows, endpoints, or public OpenAPI contracts.

## Durable Repository Contract

The internal repository exposes:

- `upsert_decision`
- `get_decision`
- `list_decisions_for_scope`
- `evaluate_decision_for_future_action`

Evaluation is intentionally conservative. A stored decision can be retrieved for future review, but `future_action_allowed = false`, `allowed_to_execute = false`, and `prepared_only = true` remain fixed until a later separately approved controller slice exists.

## Stored Fields

The repository stores:

- decision id
- review packet id
- maintenance plan id
- generated and decided timestamps
- decision kind
- tenant, merchant, seller-agent, and buyer-agent scope ids where present
- scope summary
- artifact family summary
- redacted reason codes
- redacted source refs
- redacted evidence refs
- opaque reviewer reference
- next-step labels only
- non-enablement flags
- created and updated timestamps

It stores no raw artifact payloads, provider payloads, connector payloads, JWTs, credentials, private customer data, payment data, bank or card data, raw merchant private API values, secrets, production allowlists, executable URLs, or action targets.

## Fail-Closed Persistence And Evaluation

Persistence fails closed when a decision record has missing ids, unsupported decision kind, missing tenant scope, invalid timestamps, private or enabling fields, raw source or evidence refs, raw reviewer identity, unsafe next-step labels, executable posture, or false non-enablement flags.

Evaluation fails closed when the stored decision is missing or no longer satisfies the same audit-safe validation. Evaluation never approves refresh, eviction, quarantine, checkout, payment, order, hold, refund, return, shipping, provider, merchant private API, public discovery, or publication behavior.

## Migration Safety

The migration adds only one tenant-scoped table and indexes:

- tenant id
- merchant id
- seller agent id
- buyer agent id
- review packet id
- maintenance plan id
- decision kind
- decided timestamp

It includes a uniqueness guard for the same tenant/scope, review packet, decision kind, and reviewer reference. It enables and forces tenant row-level security using `agenticorg.tenant_id`, matching the C6X4 cache posture. Rollback drops only the operator decision table, indexes, and policy.

## Guardrails

C6X8 remains internal-only, non-publication, non-certifying, non-production, non-executing, fail-closed, and non-authoritative for transactions. Valid records preserve `allowed_to_execute = false`, `future_action_allowed = false`, `non_authoritative_for_transaction = true`, `no_checkout_payment_enablement = true`, `no_live_provider_enablement = true`, and `no_public_discovery_enablement = true`.

The repository can support non-binding audit review without routing every local cache interaction through Grantex. It does not make AgenticOrg a merchant system of record, and it does not make Grantex a transaction control plane.

## What C6X8 Does Not Enable

C6X8 adds no public endpoint, route, public OpenAPI runtime contract, workflow, scheduler, cron job, queue, background worker, production config, secret, public discovery enablement, checkout, payment, order, hold, refund, return, shipping execution, live provider rail, live Plural behavior, provider call, merchant private API call, allowlist, external OACP publication, or approval/readiness claim.

Operator decisions are not merchant approvals, checkout approvals, payment approvals, mandate approvals, live-provider approvals, production approvals, certification, compliance, conformance, standardization, or public launch readiness.

## Future Work

Future slices may add a separately approved internal operator workflow or maintenance runner. Those slices must remain separate from checkout, payment, provider rail, merchant private API, public OACP publication, production config, workflow scheduling, and public endpoint behavior unless explicitly approved.
