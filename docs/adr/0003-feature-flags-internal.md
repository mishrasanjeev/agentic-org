# ADR 0003: Build feature flags in-house instead of adopting LaunchDarkly

- **Status**: Accepted
- **Date**: 2026-04-11
- **Deciders**: Sanjeev, Engineering team

## Context

We needed a feature-flag system to:
1. Ship preview features to a subset of tenants safely.
2. Disable a broken feature at runtime without a redeploy.
3. Support gradual rollout (0% → 25% → 100%).

Candidates:
- **LaunchDarkly** — mature SaaS, excellent SDK. Costs scale with MAU
  and the data crosses tenant boundaries (SaaS eval server).
- **GrowthBook** — open-source, but runs as a separate service with
  its own database.
- **Unleash** — open-source, good SDK, again a separate service.
- **In-house** — small table + tiny evaluator module.

The user's standing instruction is "open-source only, no LangSmith, no
AGPL, no proprietary SaaS" (see memory: `feedback_open_source_only`).
That rules out LaunchDarkly. The operational cost of GrowthBook or
Unleash (extra service, extra DB) is disproportionate to our needs.

## Decision

Build a small in-house feature-flag system:
- Postgres table `feature_flags` (tenant_id, flag_key, enabled,
  rollout_percentage).
- Evaluator at `core/feature_flags.py` with a 30-second in-process
  TTL cache.
- Admin CRUD API at `/api/v1/feature-flags`.

Rollout uses a deterministic hash of `(flag_key, user_id)` so a given
user's bucket assignment is stable across requests — no flicker.

## Consequences

- **Good**: No extra service, no extra SaaS vendor, no extra data
  boundary.
- **Good**: Flag lookups are a single cache hit (~μs) in the hot path.
- **Good**: Flags are tenant-aware out of the box because they live in
  the same RLS-enforced database.
- **Bad**: We don't get LaunchDarkly's goal/experimentation features.
  Acceptable — we don't need them yet.
- **Bad**: Rollout percentage updates take up to 30 seconds to
  propagate across replicas because of the in-process cache.
  Acceptable for non-emergency rollouts; for emergency off-switches
  we expose a `clear_cache()` endpoint.

## Alternatives if we outgrow this

Unleash has a drop-in proxy. The table schema is compatible enough
that a one-time migration would move flags over. We don't expect to
need that within 12 months.
