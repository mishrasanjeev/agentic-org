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
9. [Agent runs paused for approval: checkpoint store and resume](#agent-runs-paused-for-approval-checkpoint-store-and-resume)

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

---

## Agent runs paused for approval: checkpoint store and resume

A standalone agent run (`POST /api/v1/agents/{id}/run`) that hits its approval
gate pauses in a LangGraph checkpoint. Two switches control what happens next:

| Switch | Scope | Default | Effect |
|---|---|---|---|
| `AGENTICORG_LANGGRAPH_CHECKPOINTER` | process (API and workers, set together) | `memory` | `memory`: checkpoints live in process memory and are lost on restart or on another replica. `postgres`: checkpoints are stored encrypted (credential-vault keyring) in `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` (migration `v6z22`). |
| `approvals.resume_agent_runs` | per tenant feature flag | off | On: deciding the approval with `approve` or `reject` resumes the paused run from its checkpoint. Off: the decision is recorded and the run stays paused. |

Enable the flag for a tenant as a tenant admin:
`POST /api/v1/feature-flags` with `{"flag_key": "approvals.resume_agent_runs", "enabled": true, "rollout_percentage": 100}`.
Resuming across restarts and replicas needs `postgres`.

**Detect:**
- API does not start and logs `langgraph_checkpointer_unavailable reason=...`; runs
  return `503 agent_checkpoint_store_unavailable`; metric
  `agenticorg_checkpointer_unavailable_total{reason}` rises.
- `agenticorg_agent_run_resumes_total{outcome="refused"|"failed"}` rises. Each
  resume writes an `agent.run.resumed` audit event and sets
  `context.checkpoint_resume` (`state`, `reason`) on the approval.

**Diagnose** (reason codes):

| Reason | Meaning | Action |
|---|---|---|
| `checkpoint_store_unreachable` | pool could not connect | check `AGENTICORG_LANGGRAPH_CHECKPOINT_DB_URL` (defaults to the DB URL), network, credentials |
| `checkpoint_schema_missing` / `checkpoint_schema_stale` / `checkpoint_schema_ahead` | checkpoint tables absent, or `checkpoint_migrations` not at the version the installed library expects | run `python scripts/alembic_migrate.py`; after a library upgrade, ship the migration for its new entries |
| `checkpoint_event_loop_mismatch` | the pool was used from an event loop that did not open it | a code defect; capture the stack and file it |
| `checkpoint_not_found` / `checkpoint_not_paused` | no checkpoint at the approval gate for this thread (run paused under `memory`, before the flag, or cleaned up) | the run cannot be resumed; re-run the agent |
| `checkpoint_not_encrypted` / `checkpoint_decrypt_failed` | stored data is plaintext, tampered with, or encrypted with a key no longer in `AGENTICORG_VAULT_KEYRING` | restore the retired key to the keyring (keep old keys until paused runs drain); treat plaintext rows as an incident |
| `checkpoint_thread_tenant_mismatch` | a thread outside the approval's tenant | should be impossible (check constraint on `hitl_queue`); treat as a security incident |
| `decision_not_resumable` | decision other than approve or reject (for example `override`) | the run stays paused; re-run if needed |
| `rejection_not_applied` | a reject decision did not fail the run at the gate | the run is not reported as completed; investigate the agent's HITL condition |

**Mitigate:** to roll back the store, set `AGENTICORG_LANGGRAPH_CHECKPOINTER=memory`
on API and workers together; runs paused in Postgres then cannot resume. To stop
resumes for a tenant, disable the flag; decisions keep working.

**Retention and cleanup:** a run resumed to completion, or into a rejection, has
its checkpoints deleted (`checkpoint_resume.checkpoint_deleted`). Everything else
stays until removed:

- every run that finished without pausing (all runs write checkpoints under `postgres`);
- runs whose approval expired or was never decided, or was decided while the flag was off;
- refused or failed resumes;
- voice sessions (`tenant:<id>:voice:<call>` threads).

Checkpoint rows carry no tenant column; run cleanup as the database owner. Delete
threads whose newest checkpoint is older than the longest approval window (4 hours
by default) plus a margin, unless a still-open approval points at them:

```sql
BEGIN;
CREATE TEMP TABLE stale_threads ON COMMIT DROP AS
WITH newest AS (
    SELECT thread_id, max((checkpoint->>'ts')::timestamptz) AS last_ts
    FROM checkpoints GROUP BY thread_id
)
SELECT n.thread_id FROM newest n
WHERE n.last_ts < now() - interval '1 day'
  AND NOT EXISTS (
      SELECT 1 FROM hitl_queue h
      WHERE h.checkpoint_thread_id = n.thread_id
        AND h.status = 'pending' AND h.expires_at > now()
  );
DELETE FROM checkpoint_writes WHERE thread_id IN (SELECT thread_id FROM stale_threads);
DELETE FROM checkpoint_blobs  WHERE thread_id IN (SELECT thread_id FROM stale_threads);
DELETE FROM checkpoints       WHERE thread_id IN (SELECT thread_id FROM stale_threads);
COMMIT;
```

An approval whose thread was cleaned up still accepts a decision; with the flag on,
the resume is refused with `checkpoint_not_found`.

