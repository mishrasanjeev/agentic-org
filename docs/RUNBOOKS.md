# Operational Runbooks

Short, action-oriented playbooks for the most common incidents. Every
runbook is structured the same way: **detect → diagnose → mitigate → fix**.
Full DR procedures live in `docs/BACKUP_AND_DR.md`.

## Table of contents

1. [API pods OOMKilled](#api-pods-oomkilled)
2. [Database connection pool exhausted](#database-connection-pool-exhausted)
3. [LLM upstream rate-limited](#llm-upstream-rate-limited)
4. [Redis eviction storm](#redis-eviction-storm)
5. [Workflow stuck in loop](#workflow-stuck-in-loop)
6. [Connector authentication failure](#connector-authentication-failure)
7. [Plural webhook backlog](#plural-webhook-backlog)
8. [Audit log trigger blocking an upgrade](#audit-log-trigger-blocking-an-upgrade)

---

## API pods OOMKilled

**Detect:** GKE `kubectl get pods -n agenticorg | grep CrashLoop`, or a
spike in the `api_memory_rss` Grafana panel.

**Diagnose:**
```
kubectl describe pod <name> -n agenticorg | grep -A 4 "Last State"
kubectl logs <name> -n agenticorg --previous | tail -200
```

Look for:
- Sudden jump in allocations around LLM response handling.
- Large document uploads being held in memory.

**Mitigate:**
```
kubectl patch deployment api -n agenticorg \
  -p '{"spec":{"template":{"spec":{"containers":[{"name":"api","resources":{"limits":{"memory":"3Gi"},"requests":{"memory":"1500Mi"}}}]}}}}'
```

**Fix:** profile the hot path, cap per-request memory, stream large
payloads to GCS instead of holding them in RAM.

---

## Database connection pool exhausted

**Detect:** HTTP 5xx spike, `TimeoutError: QueuePool limit of size X overflow Y reached`
in logs.

**Diagnose:**
```sql
SELECT state, count(*) FROM pg_stat_activity
WHERE application_name LIKE 'agenticorg%' GROUP BY state;
```
Check for long-running "idle in transaction" sessions.

**Mitigate:**
- Bounce the affected pods: `kubectl rollout restart deploy/api`.
- If caused by a runaway query, terminate it: `SELECT pg_terminate_backend(pid)`.

**Fix:** review the code path that held the transaction open, add a
timeout, verify `session.commit()` / `session.rollback()` in `finally`.

---

## LLM upstream rate-limited

**Detect:** Spike in `llm_429_total` metric or `anthropic: rate_limit_error`
in logs.

**Diagnose:** which model, which tenant? Check the cost ledger dashboard.

**Mitigate:**
1. Failover to the Gemini fallback for non-critical agents:
   `kubectl set env deployment/api LLM_PRIMARY=gemini-2.5-flash -n agenticorg`.
2. Throttle the noisy tenant via the rate-limit bucket.

**Fix:** upgrade the Anthropic tier, or split traffic across both
primary and fallback models.

---

## Redis eviction storm

**Detect:** `redis_evicted_keys` rising, session log-outs spiking.

**Diagnose:** `MEMORY USAGE`, `INFO keyspace`.

**Mitigate:**
- Increase Memorystore size: `gcloud redis instances update agenticorg-prod --size=10 --region=asia-south1`.
- Clear the least-critical namespaces first: `plural:order:*`, `cache:*`.

**Fix:** set TTL on any key that doesn't have one, split hot caches
into a separate instance.

---

## Workflow stuck in loop

**Detect:** Single workflow consuming all budget for a tenant, agent
runs > 1000 steps.

**Diagnose:** `SELECT * FROM workflow_runs WHERE status='running' AND started_at < now() - interval '1 hour';`

**Mitigate:** mark the run as `cancelled` and let the runner clean up.

**Fix:** enforce `MAX_STEPS` and `MAX_DURATION` in the workflow engine
(see roadmap item "resource limits"). Add a circuit breaker that
escalates to HITL after N iterations without progress.

---

## Connector authentication failure

**Detect:** `ConnectorAuthError` rate climbs for a specific connector
across multiple tenants.

**Diagnose:** check the vendor status page (Salesforce, HubSpot, etc.)
and our token cache TTLs.

**Mitigate:** bump the retry backoff for that connector via the config API.

**Fix:** if the vendor rotated their OAuth secret, refresh the shared
OAuth client credentials in Secret Manager.

---

## Plural webhook backlog

**Detect:** `plural_webhook_lag_seconds` > 30.

**Diagnose:**
```
kubectl logs -l app=api -n agenticorg | grep plural_webhook
```

**Mitigate:** scale the webhook worker deployment: `kubectl scale deploy/webhook-worker --replicas=4`.

**Fix:** investigate why processing is slow — usually the subscription
activation code holds a DB lock longer than expected.

---

## Audit log trigger blocking an upgrade

**Detect:** A migration fails with "audit_log is append-only — UPDATE/DELETE rejected".

**Diagnose:** The immutability trigger added in migration `v460_enterprise`
deliberately blocks mutations.

**Mitigate:** If an upgrade legitimately needs to rewrite history (e.g.,
schema column rename), temporarily disable the trigger inside a
transaction:

```sql
BEGIN;
ALTER TABLE audit_log DISABLE TRIGGER audit_log_immutable;
-- ... your migration ...
ALTER TABLE audit_log ENABLE TRIGGER audit_log_immutable;
COMMIT;
```

**Fix:** wrap the operation in an alembic migration so the disable/
enable pair is reviewed like any other schema change. Every such
disable must be documented in the PR description.
