# Commerce Agent C6X4 Durable OACP Cache Repository

## Scope

C6X4 adds the first durable AgenticOrg OACP artifact cache repository foundation. It stores local cache records shaped by C6X2/C6X3 after fail-closed validation and keeps evaluation local for non-binding preview, answer, and prepare flows.

## Correct Ownership Model

AgenticOrg remains the buyer and seller AI-agent runtime and owns local/durable OACP artifact cache behavior. Grantex remains the trust, protocol, policy, and canonical OACP artifact authority. Merchant systems remain operational sources of record. Provider and fintech rails own mandate and payment execution where separately approved.

## Durable Repository Contract

The durable repository exposes async `upsert`, `get`, `list_for_scope`, and `evaluate` methods. It stores buyer agent, seller agent, tenant, and merchant scoped cache records without calling Grantex for every non-binding turn. Evaluation delegates to the C6X2 fail-closed cache evaluator.

## Stored Fields

The table stores `cache_record_id`, artifact identity and family, issuer and authority, tenant, merchant, seller-agent, and buyer-agent scope ids, source refs, redacted evidence refs, generated, issued, cached, expiry, freshness, revocation snapshot, TTL, risk, blocked and unsupported capabilities, verifier result refs, non-enablement flags, and created/updated timestamps.

## Migration Decision

C6X4 adds a narrow Alembic migration, `v6x4_oacp_cache`, because durable persistence is explicitly required for this slice and the repo has an established Alembic migration convention. The migration creates only `oacp_artifact_cache_records`, tenant-safe indexes, a duplicate artifact/scope uniqueness guard, timestamp checks, non-execution flag checks, and tenant RLS. Rollback drops only this cache table and indexes.

## Fail-Closed Storage And Evaluation

The repository refuses missing local ids, missing artifact ids, missing issuer or authority, missing scope, invalid timestamps, unsafe refs, executable flags, false non-enablement flags, stale freshness, ambiguous revocation snapshots, or invalid TTL policy. Final commitment actions remain prepared-only or refused, and `allowed_to_execute = false` is preserved.

## Guardrails

C6X4 stores only public-safe, non-sensitive refs. It stores no raw provider payloads, raw connector payloads, raw JWTs, credentials, tokens, private keys, bank or card data, private customer data, private merchant API values, secrets, production allowlists, or executable targets.

## What C6X4 Does Not Enable

C6X4 adds no public endpoint, public OpenAPI runtime contract, workflow, cloud resource, production config, secret, public discovery enablement, checkout, payment, order, hold, refund, return, shipping execution, no live provider rail enablement, merchant private API execution, external OACP publication, or approval/readiness claim.

## Future Work

Future slices may add cache eviction, refresh scheduling, and tenant-indexed maintenance jobs. Those must remain separate from checkout, payment, provider rail, merchant private API, and public OACP publication behavior unless separately approved.
