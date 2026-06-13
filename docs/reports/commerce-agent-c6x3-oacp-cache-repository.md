# Commerce Agent C6X3 OACP Cache Repository

## Scope

C6X3 adds an internal AgenticOrg OACP artifact cache repository port and a non-durable in-memory adapter for tests. The repository stores, reads, lists, and evaluates local cache records already shaped by C6X2. It is internal, non-publication, non-certifying, non-production, and non-executing.

## Correct Ownership Model

AgenticOrg remains the buyer and seller AI-agent runtime and owns local/persistent OACP artifact cache behavior. Grantex remains the trust, protocol, policy, and canonical OACP artifact authority. Merchant systems remain operational sources of record. Provider and fintech rails own mandate and payment execution where separately approved.

The repository allows valid cached OACP artifacts to support non-binding preview and prepare flows without routing every non-binding turn through Grantex. It does not turn cached artifacts, adapter previews, or seller cards into transaction authority.

## Repository Port

The internal port supports `upsert`, `get`, `list_for_scope`, and `evaluate`. It stores only local cache records that preserve artifact id/type, authority/issuer, buyer-agent/seller-agent/tenant/merchant scope, source refs, evidence refs, generated_at, cached_at, expires_at, freshness, revocation snapshot posture, TTL policy, risk tier, blocked capabilities, unsupported capabilities, verifier result refs, and non-enablement flags.

The in-memory adapter is a test adapter only. It makes no external calls and does not persist production records.

## Repository Query

Queries can filter by cache scope kind, tenant id, merchant id, seller agent id, buyer agent id, artifact type, and authority. The query model keeps buyer-agent, seller-agent, tenant, and merchant cache boundaries explicit.

## Evaluation Behavior

Repository evaluation delegates to the C6X2 fail-closed cache evaluator. Valid cached records may support non-binding preview from local cache while TTL and revocation snapshot posture remain acceptable. Final commitment requests are prepared-only or refused. Every evaluation keeps `allowed_to_execute = false`, `non_authoritative_for_transaction = true`, `no_checkout_payment_enablement = true`, `no_live_provider_enablement = true`, and `no_public_discovery_enablement = true`.

## Persistence And Migration Decision

C6X3 adds no DB migration. The repository port defines the runtime boundary for a future durable implementation, but production storage requires a later explicit migration proposal with table fields, tenant indexes, retention, rollback, and tests.

## Fail-Closed Rules

The repository refuses records with missing local id, artifact id, authority, issuer, required scope ids, missing refs, private/raw refs, false non-enablement flags, executable posture, or unsafe labels. Missing records evaluate as blocked. Scope mismatches, stale records, expired records, revoked records, and ambiguous revocation posture remain fail-closed in the C6X2 evaluator.

## Guardrails

C6X3 adds no public endpoint, public OpenAPI runtime contract, migration, workflow, cloud resource, production config, secret, production allowlist assignment, public discovery enablement, no checkout or payment enablement, no live provider rail enablement, no merchant private API execution, carrier/shipping path, provider call, raw connector/provider/private payload exposure, or external OACP publication/submission.

## What C6X3 Does Not Enable

C6X3 does not make OACP public. It does not create checkout, payment, provider, mandate, order, hold, refund, return, shipping, public discovery, production Commerce V1, or live rail behavior. It does not approve merchant, checkout, payment, mandate, provider, public launch, production, or future execution use.

## Future Work

Future slices may propose a durable repository implementation, cache compaction, cache eviction, refresh scheduling, or tenant-indexed storage. Any durable storage work must be explicitly approved as a migration slice before production persistence is introduced.
