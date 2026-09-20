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
10. [Governed case push: dead letters and replay](#governed-case-push-dead-letters-and-replay)
11. [Provider webhooks: verification failures and replays](#provider-webhooks-verification-failures-and-replays)

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

`approvals.resume_agent_runs` is an operator-managed authority flag: the
tenant feature-flag API refuses it (`403 flag_key_reserved`). A platform
operator enables it for a tenant with
`python scripts/authority_flags.py set approvals.resume_agent_runs --tenant <tenant id> --operator <name>`.
A global row that disables it keeps runs paused for every tenant; otherwise the
tenant row decides, else the global row.
Resuming across restarts and replicas needs `postgres`.

Operating the Postgres store:

- The API opens the store at startup and refuses to start without it. Celery
  workers open it on their first agent run (never at worker start), so an
  outage fails those runs with a reason code but does not stop workers or
  other queues.
- The store reads `AGENTICORG_VAULT_KEYRING` when it opens. **Any keyring
  change (adding, reordering or removing a key) needs a restart of the API and
  every worker**; a process that is not restarted keeps encrypting with the
  old active key.
- The key rotation tools (`core/crypto/rewrap.py`, `core/crypto/verify_all.py`)
  do **not** cover checkpoint tables (FINDINGS A-21): `verify_all` can report a
  key unreferenced while paused runs still need it, and rewrap never moves
  checkpoints to the active key. Keep a retired key in the keyring for longer
  than the approval window plus checkpoint retention.
- Each encrypted payload is bound to its thread and namespace; a blob copied
  into another thread fails with `checkpoint_binding_mismatch`.
- `langgraph-checkpoint-postgres` and `langgraph-checkpoint` are pinned exactly;
  a different installed version is refused (`checkpoint_library_unverified`).

**Detect:**
- API does not start and logs `langgraph_checkpointer_unavailable reason=...`; runs
  return `503 agent_checkpoint_store_unavailable`; metric
  `agenticorg_checkpointer_unavailable_total{reason}` rises.
- `agenticorg_agent_run_resumes_total{outcome="refused"|"failed"}` rises. Each
  resume writes an `agent.run.resumed` audit event and sets
  `context.checkpoint_resume` (`state`, `reason`) on the approval.
- `agenticorg_agent_run_resumes_total{outcome="skipped"}` and the warning
  `agent_run_resume_skipped reason=resume_flag_off_or_unavailable`: an approve or
  reject decision left a paused run paused because the flag is off for the
  tenant or the flag lookup failed.

**Diagnose** (reason codes):

| Reason | Meaning | Action |
|---|---|---|
| `checkpoint_store_unreachable` | pool could not connect | check `AGENTICORG_LANGGRAPH_CHECKPOINT_DB_URL` (defaults to the DB URL), network, credentials |
| `checkpoint_schema_missing` / `checkpoint_schema_stale` / `checkpoint_schema_ahead` | checkpoint tables absent, or `checkpoint_migrations` not at the version the installed library expects | run `python scripts/alembic_migrate.py`; after a library upgrade, ship the migration for its new entries |
| `checkpoint_library_unverified` | the installed checkpoint library is not the version the sealed saver was verified against | reinstall from the pinned requirements; an upgrade needs a reviewed code change |
| `checkpoint_encryption_key_invalid` / `checkpoint_encryption_key_missing` | `AGENTICORG_VAULT_KEYRING` is malformed or empty | fix the keyring (`id:secret,...`), then restart API and workers |
| `checkpoint_event_loop_mismatch` | the pool was used from an event loop that did not open it | a code defect; capture the stack and file it |
| `checkpoint_not_found` / `checkpoint_not_paused` | no checkpoint at the approval gate for this thread (run paused under `memory`, before the flag, or cleaned up) | the run cannot be resumed; re-run the agent |
| `checkpoint_not_encrypted` / `checkpoint_decrypt_failed` | stored data is plaintext, tampered with, or encrypted with a key no longer in `AGENTICORG_VAULT_KEYRING` | restore the retired key to the keyring (keep old keys until paused runs drain); treat plaintext rows as an incident |
| `checkpoint_binding_mismatch` / `checkpoint_binding_missing` | a payload stored under another thread was loaded, or a load bypassed the saver | treat a mismatch as a security incident (rows were copied between threads); a missing binding is a code defect |
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

**Tenant offboarding:** `core.langgraph.checkpointer.delete_tenant_checkpoints(tenant_id)`
deletes every `tenant:<id>:` thread (runs and voice sessions) from the configured
store; run it with the backend settings of the API. No offboarding flow calls it
yet, and subject-level DSAR erasure does not reach checkpoint content (FINDINGS
A-22), which is removed only by deleting the thread.

---

## Governed case push: dead letters and replay

Case hand-offs (`core/cases/push.py`, `docs/governance/case-hand-off.md`) are written to
`case_push_outbox` with the case change, so a receiver outage never loses a case. They are
delivered by the `dispatch_case_pushes` Celery task (queued after each change) and the
`sweep_case_pushes` beat task every 30 seconds (`AGENTICORG_CASE_PUSH_SWEEP_ENABLED=true`).

**Detect:** alert `case_push_dead_letters_present` (gauge
`agenticorg_case_push_dead_letter_backlog > 0`); a rise in
`agenticorg_case_push_dead_letters_total{reason}` or in
`agenticorg_case_push_deliveries_total{outcome="retry_scheduled"}`; the receiver reports missing
cases.

**Diagnose:**
- `GET /api/v1/case-push/dead-letters` (tenant admin) lists dead-lettered events with `attempts`,
  `last_status_code` and `last_error`:
  - `endpoint_rejected:http_4xx` - the receiver refused the delivery (bad signature on its side,
    wrong URL, schema rejected). Not retried.
  - `max_attempts_exceeded:<error>` - the receiver was unreachable or answered 5xx, 408, 425 or 429
    through every retry (about 17 minutes of backoff across 10 attempts).
  - `payload_invalid` - the case did not produce a valid `case_push` document; the case itself is
    unaffected. Check the API logs for `case_push_payload_invalid`.
- `GET /api/v1/governed-cases/{case_ref}/push-deliveries` shows every event for one case.
- Rows stuck `pending` with `next_attempt_at` far in the past mean no worker is delivering: check
  the Celery `delivery` queue and that the sweep is enabled.

**Mitigate:**
- Fix the receiver (URL, TLS, signing key). To change the URL: `PUT /api/v1/case-push/endpoint`.
- The system of record can pull the current document at any time:
  `GET /api/v1/governed-cases/{case_ref}/push-payload` (same event id as the push for that case
  version).

**Fix / replay:** once the receiver is healthy,
`POST /api/v1/case-push/dead-letters/{outbox_id}/replay` for each dead letter. Replay keeps the
event id, so a receiver that already processed it de-duplicates; it resets attempts and delivers
immediately. A `payload_invalid` event is rebuilt from the case on replay and refused again with
422 if the case still cannot produce a valid document.

**Signing key rotation:** `POST /api/v1/case-push/endpoint/rotate-key` returns the new secret once;
deliveries are then signed with both keys. After the receiver accepts the new key,
`POST /api/v1/case-push/endpoint/retire-previous-keys`. If a secret leaked, rotate and retire
immediately and ask the receiver to drop the old key.

---

## Provider webhooks: verification failures and replays

Inbound provider events arrive at
`POST /api/v1/webhooks/providers/{tenant_id}/{provider}/{path_token}`
(`core/cases/provider_webhooks.py`). A webhook never changes a case: only a delivery that reaches
the tenant's own inbox path **and** verifies triggers a re-investigation of matching cases awaiting
a decision, which re-reads everything from the provider.

**Detect:** `agenticorg_provider_webhook_receipts_total{outcome="unverified"}` rising (forged,
stale, tampered or unsigned deliveries, or a signing secret that no longer matches);
`outcome="duplicate"` rising (the provider or an attacker is replaying event ids);
`outcome="unbound"` rising (deliveries to a wrong or stale inbox path - usually a provider still
configured with an old path after `AGENTICORG_SECRET_KEY` was rotated, otherwise scanning).

**Diagnose:** `provider_webhook_receipts` records every delivery's outcome, event id (verified only),
body SHA-256 and how many cases it re-queried; bodies are never stored, and unbound deliveries are
not recorded at all (they are counted). A sudden switch from `accepted` to `unverified` for one
provider usually means its webhook secret was rotated on one side only; a switch to `unbound` means
the path changed.

**Mitigate:** the route answers 202 for every outcome and is rate limited (`provider-webhook`).
Unverified deliveries cost one receipt row each and nothing else - they never start an
investigation. If a provider is compromised, set its webhook secret to a new value (or stop routing
its traffic); to invalidate every inbox path at once, rotate `AGENTICORG_SECRET_KEY` and give each
tenant's provider the new path from `GET /api/v1/case-push/provider-webhook-inbox`.

**Fix:** align the provider's webhook secret with its configuration (for the mock:
`AGENTICORG_MOCK_PROVIDER_WEBHOOK_SECRET`) and its delivery URL with the tenant's current inbox
path. Missed events are harmless to replay from the provider: a verified event id is processed once;
a new id triggers one re-investigation. A re-investigation that cannot reach the provider leaves the
case in `awaiting_decision` with its previous memo and the transition reason
`re_evaluation_failed:<reason>`; replay the event once the provider is back.

