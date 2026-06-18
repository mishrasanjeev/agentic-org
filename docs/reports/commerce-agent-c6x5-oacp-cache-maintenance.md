# Commerce Agent C6X5 OACP Cache Maintenance Planner

## Scope

C6X5 adds an internal AgenticOrg planner over durable OACP cache records. The planner classifies local cache records into refresh, eviction, quarantine, review, or keep outcomes. It is planner only: it does not refresh, evict, purge, schedule, call Grantex live, call providers, or call merchant systems.

C6X5 does not call Grantex live. It consumes existing local durable cache records and returns maintenance recommendations only.

C6X5 does not call providers. It also does not call merchant private APIs, carriers, shipping providers, or payment rails.

## Correct Ownership Model

AgenticOrg remains the buyer and seller AI-agent runtime and owns local and durable OACP artifact cache behavior. Grantex remains the trust, protocol, policy, and canonical OACP artifact authority. Merchant systems remain operational sources of record. Provider and fintech rails own mandate and payment execution where separately approved.

## Maintenance Planner Inputs

Inputs are current time, Grantex availability, action intent, risk tier, optional max batch size, optional buyer-agent, seller-agent, tenant, merchant, or artifact filters, and C6X4 cache records. The planner preserves cache scopes for buyer agent, seller agent, tenant, and merchant records.

## Maintenance Outcomes

Planner outcomes are `keep_usable`, `refresh_recommended`, `refresh_required_before_commitment`, `evict_expired`, `purge_revoked`, `quarantine_ambiguous_revocation`, `quarantine_scope_mismatch`, `quarantine_private_or_raw_ref`, `source_refresh_needed`, `human_review_required`, and `blocked_unsafe`.

## Plan Output

The plan includes a deterministic local plan id, generated time, record counts, record ids grouped by maintenance action, per-record reason codes, public-safe source refs, public-safe evidence refs, `allowed_to_execute = false`, `non_authoritative_for_transaction = true`, and no checkout, live provider, or public discovery enablement flags.

## Fail-Closed Rules

The planner fails closed for missing identity, missing scope, mismatched scope, invalid timestamps, expired records, revoked records, ambiguous revocation snapshots, stale freshness, private or raw refs, executable flags, false non-enablement flags, critical risk, unsupported transaction authority, and stricter final-commitment freshness needs.

## Migration And Scheduler Decision

C6X5 adds no migration and no scheduler. It reuses C6X4 durable cache records and produces maintenance plans only. Any future durable maintenance log, cron job, queue, or background worker requires a separate approved slice.

## Guardrails

C6X5 stores nothing new and performs no side effects. It carries only public-safe source and evidence refs and excludes raw provider payloads, raw connector payloads, raw JWTs, credentials, tokens, private keys, bank or card data, private customer data, private merchant API values, secrets, production allowlists, or executable targets.

## What C6X5 Does Not Enable

C6X5 adds no public endpoint, public OpenAPI runtime contract, workflow, cron, scheduler, queue, background worker, cloud resource, production config, secret, public discovery enablement, checkout, payment, order, hold, refund, return, shipping execution, live provider rail enablement, merchant private API execution, external OACP publication, or approval claim.

## Future Work

Future slices may implement a separately approved internal maintenance runner, refresh intent queue, or eviction audit trail. Those must stay separate from checkout, payment, provider rail, merchant private API, and public OACP publication behavior unless separately approved.
