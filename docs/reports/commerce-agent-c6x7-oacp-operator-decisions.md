# Commerce Agent C6X7 OACP Operator Decisions

## Scope

C6X7 adds internal operator decision intake over C6X6 OACP cache maintenance review packets. It produces an operator decision record only. It does not refresh artifacts, evict cache records, quarantine records, schedule maintenance, and does not call Grantex live, providers, merchant systems, connector systems, or expose an API.

C6X7 consumes an existing local C6X6 `operator_review_packet` and records label-only future intent. It is not a maintenance runner and it is not a durable audit log.

## Correct Ownership Model

AgenticOrg remains the buyer and seller AI-agent runtime and owns local and durable OACP artifact cache behavior. Grantex remains the trust, protocol, policy, and canonical OACP artifact authority. Merchant systems remain operational sources of record. Provider and fintech rails own mandate and payment execution where separately approved.

Grantex is not required in every non-binding buyer or seller agent turn. Valid cached OACP artifacts and local review packets can support non-binding and prepare-only flows without turning Grantex into a transaction toll booth.

## Decision Kinds

C6X7 decision kinds are `approve_future_refresh_request`, `approve_future_eviction_request`, `approve_future_quarantine_request`, `request_more_evidence`, `reject_maintenance_action`, `defer_until_freshness_update`, `escalate_to_human_support`, and `block_unsafe_action`.

The `approve_future_*` decisions are future-only labels. They are not merchant approval, payment approval, checkout approval, mandate approval, production approval, or permission to execute cache maintenance in C6X7.

## Decision Record Output

Decision records include decision id, review packet id, maintenance plan id, generated and decided timestamps, decision kind, scope summary by buyer agent, seller agent, tenant, and merchant, artifact families and types affected, redacted reason codes, redacted source refs, redacted evidence refs, opaque reviewer reference, next-step labels, buyer-safe message, seller-safe message, and operator-safe message.

Every decision record keeps `allowed_to_execute = false`, `non_authoritative_for_transaction = true`, `no_checkout_payment_enablement = true`, `no_live_provider_enablement = true`, and `no_public_discovery_enablement = true`.

Reviewer identity is represented only by an opaque reviewer reference. Raw contact details, credentials, tokens, private payloads, and customer identifiers are not allowed.

## Fail-Closed Rules

C6X7 returns a blocked decision record when the review packet is missing, malformed, not an `operator_review_packet`, executable, unsafe, missing non-enablement flags, missing redacted refs, contains private or raw values, includes a non-opaque reviewer reference, requests an unsupported or immediate action, or attempts publication or launch claims.

Stale, revoked, ambiguous, or high-risk records may only receive future-only or review-only labels. C6X7 does not convert a stale, revoked, or high-risk packet into executable maintenance behavior.

## Migration Scheduler And Persistence Decision

C6X7 adds no migration and no scheduler. It stores no decision records, creates no cron job, creates no queue, and adds no background worker. Durable decision persistence or maintenance execution requires a later separately approved slice.

## Guardrails

C6X7 carries only public-safe source and evidence refs. It excludes raw artifact payloads, raw provider payloads, raw connector payloads, raw JWTs, credentials, tokens, private keys, bank or card data, private customer data, private merchant API values, secrets, production allowlists, executable URLs, checkout/payment enablement, live provider rail enablement, public discovery enablement, external OACP publication, certification, conformance, standardization, and readiness claims.

## What C6X7 Does Not Enable

C6X7 adds no public endpoint, public OpenAPI runtime contract, workflow, cron, scheduler, queue, background worker, cloud resource, production config, secret, public discovery enablement, checkout, payment, order, hold, refund, return, shipping execution, live provider rail enablement, merchant private API execution, external OACP publication, approval claim, certification claim, conformance claim, standardization claim, or production-readiness claim.

## Future Work

Future slices may add a separately approved durable decision log, operator workflow, or maintenance runner. Those must stay separate from checkout, payment, provider rail, merchant private API, public OACP publication, and production enablement unless separately approved.
