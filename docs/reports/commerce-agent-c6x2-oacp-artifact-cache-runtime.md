# Commerce Agent C6X2 OACP Artifact Cache Runtime

## Scope

C6X2 adds an internal AgenticOrg persistent OACP artifact cache helper/model boundary. The helper evaluates local cache records for non-binding preview and prepared-only handoff behavior. It is internal, non-publication, non-certifying, non-production, and non-executing.

## Correct Ownership Model

AgenticOrg remains the buyer and seller AI-agent runtime. AgenticOrg owns buyer agents, seller agents, channel UX, local and persistent OACP artifact cache behavior, seller-agent initiated connector sync intent, and runtime consumption of OACP artifacts. Grantex remains the trust, protocol, policy, and canonical OACP artifact authority. Merchant systems remain operational sources of record. Provider and fintech rails own mandate and payment execution where separately approved.

The cache model supports valid cached OACP artifacts without routing every non-binding turn through Grantex. It does not make cached artifacts, adapter previews, or seller cards transaction authority.

## Persistent Cache Record Model

The internal record model includes cache record id, artifact id, artifact family/type, authority, issuer, buyer-agent/seller-agent/tenant/merchant scope ids where applicable, source refs, evidence refs, generated_at, cached_at, expires_at, freshness status, revocation snapshot status and timestamp, TTL policy, risk tier, blocked capabilities, unsupported capabilities, verifier result reference, and non-enablement flags.

The four cache scopes are buyer agent, seller agent, tenant, and merchant. Each scope must carry the required local ids before cache evaluation can continue.

## Cache Evaluation Helper

The helper evaluates one local record at a time. It returns a deterministic local decision with allowed_to_preview, allowed_to_prepare, allowed_to_execute, prepared_only, non_authoritative_for_transaction, no_checkout_payment_enablement, no_live_provider_enablement, no_public_discovery_enablement, buyer-safe message, seller-safe message, source refs, evidence refs, freshness, revocation, and risk metadata.

`allowed_to_execute` is always false in C6X2. The helper does not call Grantex, providers, merchant private APIs, checkout/payment systems, public discovery services, or any external system.

## Freshness, Revocation, And TTL

Valid cached records may support non-binding preview or answer flows while TTL and revocation snapshot posture are acceptable. Freshness must be fresh or provisional, timestamps must parse, expires_at must be in the future, and TTL policy must not exceed the internal OACP artifact defaults. Revocation snapshot posture must be fresh and within the risk-tier maximum age.

## Non-Binding Versus Commitment-Bound Use

Non-binding preview can continue from valid cache even when Grantex is unavailable. Prepare-only handoff can continue from valid cache and source-aware metadata. Final commitment requests remain prepared-only or refused; C6X2 does not execute, approve, create, hold, refund, return, ship, publish, or settle anything.

Adapter previews, seller cards, and public discovery records cannot override missing, expired, revoked, or stale canonical artifacts and cannot become transaction authority.

## Persistence And Migration Decision

C6X2 implements a pure internal model/helper boundary and focused tests only. It adds no DB migration. A later migration proposal is required before durable production storage, including table fields, indexes, tenant boundaries, retention, rollback, tests, and risk review.

## Fail-Closed Rules

The helper fails closed for missing cache records, missing artifact id, missing authority or issuer, missing scope ids, stale or expired timestamps, stale or ambiguous revocation snapshots, revoked records, mismatched scope, missing evidence/source refs, private/raw refs, secrets or token-like refs, false non-enablement flags, critical risk tier, final commitment against adapter/seller-card/discovery records, and final commitment without stronger verifier evidence.

## Guardrails

C6X2 adds no public endpoint, public OpenAPI runtime contract, migration, workflow, cloud resource, production config, secret, production allowlist assignment, public discovery enablement, no checkout or payment enablement, no live provider rail enablement, no merchant private API execution, carrier/shipping path, provider call, raw connector/provider/private payload exposure, or external OACP publication/submission.

## What C6X2 Does Not Enable

C6X2 does not make OACP public. It does not create checkout, payment, provider, mandate, order, hold, refund, return, shipping, public discovery, production Commerce V1, or live rail behavior. It does not approve merchant, checkout, payment, mandate, provider, public launch, production, or future execution use.

## Future Work

Future slices can propose durable cache storage, cache eviction, tenant indexes, revocation refresh, source sync scheduling, and controlled handoff integration. Those slices must stay separate from live execution until a future product decision explicitly approves the necessary runtime, security, and operational changes.
