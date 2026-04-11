# Scaling Guide

How to grow the managed SaaS deployment from the current footprint
(3 API pods, 1 DB instance) to 10× load.

## Current baseline (v4.6.0)

| Component     | Replicas | Resource limits    | Notes |
|---------------|----------|--------------------|-------|
| API pods      | 3        | 2Gi mem, 1 CPU     | HPA to 20 on p90 latency |
| Worker pods   | 2        | 2Gi mem, 1 CPU     | HPA to 10 on queue depth |
| PostgreSQL    | 1 primary + 1 replica | 8 vCPU, 32 GB | Cloud SQL Enterprise Plus |
| Redis         | 1        | 5 GB               | Memorystore, no persistence |
| WebSocket feed| 2        | 512Mi mem, 0.5 CPU | Sticky sessions |
| RAGFlow       | 2        | 4Gi mem, 2 CPU     | GKE nodepool |

## Horizontal scaling

### API

The Horizontal Pod Autoscaler (`helm/values-production.yaml`) targets
p90 latency < 500ms. To raise the ceiling:

```
kubectl patch hpa api -n agenticorg -p '{"spec":{"maxReplicas":40}}'
```

Past ~20 pods, the bottleneck shifts to the DB connection pool. Add
a PgBouncer sidecar before going further — see the "Database"
section below.

### Worker pods

Workers consume the Celery queue (cost ledger aggregation, report
generation, webhook processing). Scale on queue depth:

```
kubectl patch hpa worker -n agenticorg -p '{"spec":{"maxReplicas":20}}'
```

### WebSocket feed

Sticky sessions mean scaling these pods requires an update to the
nginx config. Rule of thumb: 1 pod per 2000 concurrent websocket
clients. For higher loads, shard by tenant across two deployments.

## Vertical scaling

### PostgreSQL

- **Up to 2M rows per tenant**: current instance is fine.
- **2M–20M rows per tenant**: upgrade to 16 vCPU / 64 GB and enable
  `synchronous_commit = off` for the audit log (already off for us).
- **>20M rows per tenant**: shard by tenant (see "Sharding" below).

### Redis

- **Up to 5000 concurrent sessions**: 5 GB is fine.
- **5000–20000**: bump to 10 GB and enable Redis Cluster.
- **>20000**: split session/cache from rate-limiting state into two
  instances.

## Sharding

If a single tenant exceeds 20M agent task rows, shard the large tables
by `tenant_id`:

1. Create a new Cloud SQL instance (`agenticorg-prod-shard-2`).
2. Use the existing `app.tenant_routing` GUC we set in
   `core/database.py` to select the connection at request time.
3. Backfill the tenant's data using `pg_dump --data-only --table=<t>`.
4. Update the `tenant_shards` lookup table.
5. Remove rows from the original shard once verified.

This is operationally expensive. Usually it's cheaper to upgrade the
instance first.

## Capacity planning worksheet

For sizing a new deployment, start from these numbers:

| Metric                              | Per 1000 MAU |
|-------------------------------------|--------------|
| Peak QPS                            | 5–10         |
| LLM requests/day                    | 2000         |
| Database rows written/day           | 50,000       |
| GCS objects written/day             | 500          |
| Redis keys at peak                  | 10,000       |
| p95 LLM cost / day (Claude Sonnet)  | ~$4          |

Multiply by expected MAU, add 2× buffer, round up to the next instance
size.

## When to scale vs. optimize

- **Latency > 1s p90**: profile first. Don't throw hardware at it
  without finding the actual hot loop.
- **DB CPU > 60% for >10 min**: scale DB vCPU or add a read replica
  for the analytics queries.
- **LLM cost > budget**: apply rate-limit buckets to offending tenants
  (see `docs/incident-response.md`).
- **Memory > 80% of pod limit**: scale out first, profile second.

## Contacts

- SRE on-call: PagerDuty rotation `agenticorg-sre`
- Infra lead: Sanjeev (CTO)
