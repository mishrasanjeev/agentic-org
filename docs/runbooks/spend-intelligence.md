# AI Spend Intelligence Rollout And Enablement

This runbook takes AI spend intelligence to production in two separate changes:
first a release with the feature off, then enablement per environment when an
owner decides to turn it on. It also covers checks after enabling, pausing one
tenant, disabling and rolling back.

The behaviour is defined in the [feature reference](../finops/spend-intelligence.md);
end users read the [AI spend intelligence guide](../user-guide/ai-spend-intelligence.md).
Release mechanics follow the [deployment guide](../deployment.md) and the
[production smoke runbook](production_smoke.md). Nothing here creates new
infrastructure: the feature runs inside the existing API, worker and beat
services and the existing PostgreSQL database.

## What the switch controls

| Variable | Default | Set on | Effect |
| --- | --- | --- | --- |
| `AGENTICORG_SPEND_INTELLIGENCE_ENABLED` | `false` | API, worker, beat | The feature switch. Off, `GET /api/v1/spend/status` answers `enabled: false`, every other spend route answers 404 `spend_disabled` before its body is read, every metering site is a bool read, the spend tasks skip and nothing is written. |
| `AGENTICORG_SPEND_SWEEPS_ENABLED` | `true` | API, worker, beat (the worker runs the beat tasks and reads it) | The spend beat jobs: partition horizon, FX settlement, commitment recompute, the job sweep, storage samples and GPU allocation. No effect while the feature is off. |
| `AGENTICORG_SPEND_REPORTING_TIMEZONE` | `Asia/Kolkata` | API, worker, beat | The reporting calendar (event dates, FX dates, rollup days, coverage). Choose it before enabling: records keep the dates they were written with. |
| `AGENTICORG_SPEND_PROVIDER_BILLING_TIMEZONES_JSON` | empty | API, worker, beat | A JSON object of provider to IANA zone for billing dates; unlisted providers use the built-in defaults, then UTC. |
| `AGENTICORG_SPEND_GPU_POOLS_JSON` | empty | API, worker, beat | Standing in-house GPU pools whose node hours are spread over the pool's calls. Leave empty when nothing is served in house. |
| `AGENTICORG_SPEND_AUDIT_COMMERCIAL_FILTER` | `true` | API | `GET /api/v1/audit` hides rate-card, commitment, invoice, reconciliation and spend job audit rows from readers who are not a human administrator or auditor, whatever the feature switch says. `false` is the explicit opt-out: the previous `GET /audit` behaviour for every reader, those rows included. |

With the feature on, a process refuses to start when a zone cannot be loaded
or the GPU pools do not parse. Off, none of these settings is read except
`AGENTICORG_SPEND_AUDIT_COMMERCIAL_FILTER`, which applies whatever the switch
says because audit rows outlive it.

Phase 1 is API-only: there is no spend screen in the application. Spend
dashboards, budgets, spend alerts, forecasting, statements and month close are
later phases.

## 1. Release with the feature off

### Before the release

Set the environment you are working in. Never enable shell tracing while a
token is in the shell, and never print a token.

```bash
export GCP_PROJECT_ID=<project id>
export CLOUD_RUN_REGION=<Cloud Run region>
export DEPLOY_SHA=<full commit to release>
export APP=<the environment's API origin, for example https://app.agenticorg.ai>
# The deploy script checks the new commit at HEALTH_URL, which otherwise defaults to production.
export HEALTH_URL="$APP/api/v1/health"
# The service and job names; change them where the environment's names differ.
export API_SERVICE=agenticorg-api UI_SERVICE=agenticorg-ui
export WORKER_SERVICE=agenticorg-worker BEAT_SERVICE=agenticorg-beat
export MIGRATE_JOB=agenticorg-migrate
```

Run every step from a clean checkout at `$DEPLOY_SHA`: the deploy script refuses
a checkout whose HEAD differs or that has uncommitted or untracked files (see
the [deployment guide](../deployment.md)). In staging, `HEALTH_URL` must point at
staging; left at its default, the script would poll production health, miss the
new commit and roll staging back, or pass falsely when production already serves
that commit.

Confirm that no service already sets a spend variable. This lists variable
names only, never values:

```bash
for svc in "$API_SERVICE" "$WORKER_SERVICE" "$BEAT_SERVICE"; do
  gcloud run services describe "$svc" --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
    --format='value(spec.template.spec.containers[0].env[].name)' |
    tr ';' '\n' | grep '^AGENTICORG_SPEND_' | sed "s/^/$svc: /"
done
```

The expected output is empty. If `AGENTICORG_SPEND_INTELLIGENCE_ENABLED` is
present on any service, remove it (see [Disable](#4-disable)) before the release,
or the release turns the feature on.

Record the revisions each service serves now, so a rollback has a target. The
deploy script prints them as `Previous ... traffic`; keep that output with the
change record.

### Deploy

```bash
bash scripts/deploy_cloud_run.sh --sha "$DEPLOY_SHA" --skip-build --with-migrations --yes
```

The script runs the migration job before it stages any service, then rolls the
API, the worker and beat, and the UI as described in the
[deployment guide](../deployment.md). It sets variables with
`--update-env-vars`, so it neither adds nor removes the spend variables.

### What the four migrations create

Each migration adds tables and alters no existing table, so the revisions
serving before the release keep working while the migration job runs.

| Migration | Tables |
| --- | --- |
| `v6z79_spend_reference` | `spend_org_nodes`, `spend_source_mappings`, `spend_rate_cards`, `spend_model_aliases`, `spend_commitments`, `spend_fx_rates`: the reference data, tenant scoped under forced row-level security, with composite `(tenant_id, id)` foreign keys |
| `v6z80_spend_usage` | `spend_usage_records` (range-partitioned by month from July 2026 to December 2028, plus a default partition), `spend_usage_rollups`, `spend_meter_gaps`, `spend_jobs`: every table and partition tenant scoped under forced row-level security |
| `v6z81_spend_gpu` | `spend_gpu_pool_hours` (a platform table that holds no tenant data, so it has no `tenant_id` and no row-level policy) and `spend_gpu_allocations` (tenant scoped) |
| `v6z82_spend_reconciliation` | `spend_invoices`, `spend_invoice_lines`, `spend_reconciliations`, `spend_reconciliation_items`, tenant scoped under forced row-level security |

Afterwards `alembic_version` holds `v6z82_spend_reconciliation`. The tables stay
empty while the feature is off.

### Verify the release with the feature off

Use the bearer token of a tenant administrator or auditor (the status route
needs `audit:read`; an unauthenticated request is refused with 401 before the
spend gate answers):

```bash
read -rs TOKEN   # paste the token; it is not echoed
curl -fsS "$APP/api/v1/health" | python -m json.tool | grep -E '"(status|commit)"'
curl -sS -H "Authorization: Bearer $TOKEN" "$APP/api/v1/spend/status" | python -c 'import json, sys; print(json.load(sys.stdin)["enabled"])'
curl -sS -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" "$APP/api/v1/spend/org-nodes"
curl -sS -H "Authorization: Bearer $TOKEN" "$APP/api/v1/spend/rate-cards"
unset TOKEN
```

Expected:

- health reports `healthy` and `$DEPLOY_SHA`;
- the status prints `False`;
- `GET /api/v1/spend/org-nodes` returns `404`, and the rate-card read answers
  `{"detail": {"error": "spend_disabled", ...}}`; every spend route except the
  status behaves the same way;
- `GET /api/v1/audit` answers as before. Its commercial filter only applies to
  a tenant that keeps a rate card, commitment, invoice, reconciliation or spend
  job, and no tenant has one yet.

The release is complete at this point. Leave the feature off until an owner
approves enablement for a named environment.

## 2. Enable in an environment

Enable in staging first and run the checks in [section 3](#3-check-after-enabling)
there before production.

### Before you enable

- **Owner approval** for the environment, recorded with the change.
- **Administrators.** Every spend write needs a tenant administrator signed in
  as a person; API keys and agent tokens are refused. Accepting a
  reconciliation difference needs an administrator other than the one who
  imported the invoice, so a tenant with one administrator cannot accept
  differences.
- **Settings.** Decide the reporting zone, provider billing zones and GPU pools
  now. A value that does not load stops the new revision from starting, and the
  previous revision keeps its traffic because the steps below stage with
  `--no-traffic`.
- **Alerts.** Load `monitoring/prometheus/agenticorg-alerts.yml` so
  `AgenticOrgSpendWriteFailures` and `AgenticOrgSpendWriterBacklog` are active
  for the environment.
- **Backfill source.** Model-call records can be recreated later only from
  model gateway records while the gateway and its records are on and retained.
  The status route reports `backfill_source` (`model_gateway_records` or
  `none`).

### Turn it on: worker, then beat, then the API

Order matters. The API's writer hands batches it cannot write to the worker
(`persist_usage` on the `maintenance` queue), and a worker with the feature off
skips them. Turning the worker on first means a spilled batch always reaches a
worker that writes it.

```bash
latest_created() {
  gcloud run services describe "$1" --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
    --format='value(status.latestCreatedRevisionName)'
}

stage_spend() {
  # $1 service, $2 the --update-env-vars or --remove-env-vars argument.
  # Prints only the new revision; fails when the update fails or creates no new revision.
  before="$(latest_created "$1")" || return 1
  if ! gcloud run services update "$1" --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
    "$2" --no-traffic >&2; then
    echo "$1: the update failed; nothing was staged and traffic is unchanged" >&2
    return 1
  fi
  after="$(latest_created "$1")" || return 1
  if [ -z "$after" ] || [ "$after" = "$before" ]; then
    echo "$1: no new revision was created; traffic is unchanged" >&2
    return 1
  fi
  echo "$after"
}

route_spend() {
  # $1 service, $2 the staged revision; traffic moves only to a revision whose Ready condition is True
  ready="$(gcloud run revisions describe "$2" --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
    --format=json | python -c 'import json, sys
conditions = {c["type"]: c["status"] for c in json.load(sys.stdin)["status"].get("conditions", [])}
print(conditions.get("Ready", "Unknown"))')"
  if [ "$ready" != "True" ]; then
    echo "$2 is not ready ($ready); $1 keeps its current traffic" >&2
    return 1
  fi
  gcloud run services update-traffic "$1" --project="$GCP_PROJECT_ID" --region="$CLOUD_RUN_REGION" \
    --to-revisions="$2=100"
}

SPEND_ENV='--update-env-vars=AGENTICORG_SPEND_INTELLIGENCE_ENABLED=true'

for svc in "$WORKER_SERVICE" "$BEAT_SERVICE" "$API_SERVICE"; do
  rev="$(stage_spend "$svc" "$SPEND_ENV")" || break
  echo "$svc staged $rev"
  route_spend "$svc" "$rev" || break
done
```

The loop stops at the first service whose update fails, creates no new
revision or stages a revision that does not become ready, for example when a
spend setting does not load; that service and the ones after it keep their
previous revisions. Read the revision's log, fix the setting and run the loop
again from that service. If a service already has the same values, gcloud may
create no new revision and the loop stops there too: check its variables before
you go on.

Add the optional settings to `SPEND_ENV` when the environment needs them;
`AGENTICORG_SPEND_SWEEPS_ENABLED` stays unset, which means on. gcloud splits
`--update-env-vars` on commas, and `AGENTICORG_SPEND_GPU_POOLS_JSON` or a billing
zone object with two or more providers contains commas, so choose another
delimiter with gcloud's `^delimiter^` prefix (see `gcloud topic escaping`):

```bash
SPEND_ENV='--update-env-vars=^@^AGENTICORG_SPEND_INTELLIGENCE_ENABLED=true@AGENTICORG_SPEND_PROVIDER_BILLING_TIMEZONES_JSON=<JSON object>@AGENTICORG_SPEND_GPU_POOLS_JSON=<JSON>'
```

Do not use `--env-vars-file` for this: it replaces every variable of the
service with the file's contents.

Each `services update` keeps the other variables, secret mounts, the image and
the worker or beat command of the service. The next release with
`scripts/deploy_cloud_run.sh` keeps the spend variables for the same reason.

## 3. Check after enabling

Use an administrator or auditor token as in section 1.

1. **Status.** `GET /api/v1/spend/status` answers `enabled: true`, the
   reporting zone you chose, `backfill_source`, and `partition_horizon` with
   the months left (low below six).
2. **Writer started.** The status route's `writer.started` reports whether the
   answering instance has started its usage writer. The writer starts with the
   first metered call or meter gap in that process, so `false` straight after
   enabling is expected; run one agent or model call in the tenant and read the
   status again. The writer's pending count is not in the route because it
   spans every tenant; read the `agenticorg_spend_usage_pending` gauge.
3. **Records appearing.** After the call, read today's records as an
   administrator or auditor:

   ```bash
   zone="$(curl -sS -H "Authorization: Bearer $TOKEN" "$APP/api/v1/spend/status" |
     python -c 'import json, sys; print(json.load(sys.stdin)["reporting_timezone"])')"
   today="$(TZ="$zone" date +%F)"   # today in the reporting zone the deployment uses
   curl -sS -H "Authorization: Bearer $TOKEN" \
     "$APP/api/v1/spend/usage?start=$today&end=$today&limit=5" | python -m json.tool | head -60
   ```

   Records appear within a few seconds of the call (the writer flushes every
   200 ms). Until a tenant enters its organisation tree and mappings, records
   are kept as unattributed with a reason; until a rate card or the
   deployment's list price covers a model, they are kept as unpriced. Neither
   is an error. Entering the data later does not change records already
   written: `POST /api/v1/spend/usage/reattribute` resolves unattributed
   records again, and `POST /api/v1/spend/usage/restate` (a provider and at
   most 92 billing days) re-prices unpriced and fallback-priced records with a
   card added since. A new FX rate queues its own settlement.
   `GET /api/v1/spend/coverage` and `GET /api/v1/spend/gaps` (same `start` and
   `end`) show the attributed and unattributed shares and anything that could
   not be metered.
4. **Alerts and metrics.** `AgenticOrgSpendWriteFailures` fires when usage
   events have failed to be written for 15 minutes (any reason except
   `paused`); `AgenticOrgSpendWriterBacklog` fires when an instance holds more
   than 4,000 unwritten events for 10 minutes. Neither should fire. Watch
   `agenticorg_spend_usage_write_failures_total` by `reason`,
   `agenticorg_spend_usage_records_total`,
   `agenticorg_spend_unmetered_calls_total` and `agenticorg_spend_hook_seconds`.
   When an alert fires, follow [Writer alerts](../finops/spend-intelligence.md#writer-alerts):
   check the `reason` label, the database and the `maintenance` queue.
5. **Worker and beat.** The worker log shows the spend tasks registered and
   `persist_usage` or `run_job` completing when they run; beat logs the spend
   schedule entries. To see a job run end to end, an administrator can rebuild
   today's rollups from the records as they are
   (`POST /api/v1/spend/rollups/rebuild` with `{"start": today, "end": today}`,
   audited): the job moves from `queued` to `succeeded` in
   `GET /api/v1/spend/jobs`.

Keep the token out of logs and `unset TOKEN` when you finish.

### Pause metering for one tenant

Without a restart, a tenant's metering can be paused with the feature flag
`spend.metering_paused`: a tenant row with `enabled: true` and
`rollout_percentage: 100` (`POST /api/v1/feature-flags` as that tenant's
administrator), or a global row, which a platform operator manages, for every
tenant. The writer reads it through a 30-second cache, and the storage sample
and GPU allocation check it too. Paused usage is discarded and counted under the
`paused` reason, which the write-failure alert ignores, and it is not recorded
later. Delete or disable the row to resume.

## 4. Disable

Disabling turns metering and the spend routes off without touching data. It
uses `stage_spend` and `route_spend` from [section 2](#2-enable-in-an-environment),
defined in the same shell. Turn the API and beat off first, wait, and only then
the worker, so a batch the API spills while it shuts down still reaches a
worker that writes it. A worker with the feature off skips such a batch, and
its usage is lost without being counted as a gap.

1. Turn off the API, then beat:

   ```bash
   for svc in "$API_SERVICE" "$BEAT_SERVICE"; do
     rev="$(stage_spend "$svc" --remove-env-vars=AGENTICORG_SPEND_INTELLIGENCE_ENABLED)" || break
     echo "$svc staged $rev"
     route_spend "$svc" "$rev" || break
   done
   ```

2. Wait until the previous API revision has no instances left (its container
   instance count in Cloud Run monitoring reaches zero; each instance drains
   its writer for up to 5 seconds when it stops and spills the rest),
   then until the worker has had no `persist_usage` task for 10 minutes. A
   spilled batch retries with backoff for about four minutes, so a quiet
   10-minute window means none is left. Repeat this until it prints nothing:

   ```bash
   gcloud logging read \
     "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$WORKER_SERVICE\" AND \"spend_tasks.persist_usage\"" \
     --project="$GCP_PROJECT_ID" --freshness=10m --limit=5 --format='value(timestamp)'
   ```

3. Turn off the worker:

   ```bash
   rev="$(stage_spend "$WORKER_SERVICE" --remove-env-vars=AGENTICORG_SPEND_INTELLIGENCE_ENABLED)" &&
     echo "$WORKER_SERVICE staged $rev" &&
     route_spend "$WORKER_SERVICE" "$rev"
   ```

After disabling:

- `GET /api/v1/spend/status` answers `enabled: false` and every other spend
  route answers 404 `spend_disabled`, as in section 1;
- every spend table and row stays in the database, unchanged, and is readable
  again when the feature is turned back on;
- the `GET /api/v1/audit` commercial filter stays: rate-card, commitment,
  invoice, reconciliation and spend job audit rows remain visible only to a
  human administrator or auditor, whatever the switch says. Those audit rows
  are never deleted, so turning the switch off does not and must not expose
  them. The filter is a deliberate default (recorded in `CHANGELOG.md`); its
  explicit opt-out is `AGENTICORG_SPEND_AUDIT_COMMERCIAL_FILTER=false` on the
  API, which restores the previous `GET /audit` behaviour for every reader and
  so shows contract prices, committed amounts and invoice figures to
  domain-scoped readers and machine credentials. Set it only with the owner's
  approval recorded in the change record, staged and routed like the switch:

  ```bash
  rev="$(stage_spend "$API_SERVICE" --update-env-vars=AGENTICORG_SPEND_AUDIT_COMMERCIAL_FILTER=false)" &&
    echo "$API_SERVICE staged $rev" &&
    route_spend "$API_SERVICE" "$rev"
  ```

  Remove the variable the same way (`--remove-env-vars=AGENTICORG_SPEND_AUDIT_COMMERCIAL_FILTER`)
  to hide the rows again;
- usage while the feature is off is not metered and not counted as a gap. After
  turning it back on, `POST /api/v1/spend/usage/backfill` (at most 31 days)
  can recreate model-call records for the off period from model gateway records
  while they exist; other usage of that period stays unmetered.

## 5. Roll back a release

Rollback moves traffic, never the schema.

- If the feature is on and the rollback goes to a revision from before the
  spend release, [disable](#4-disable) it first and let the `maintenance` queue
  drain as described there. A worker from before the spend release does not
  know the `persist_usage` and `run_job` tasks and drops them, so spilled usage
  still queued would be lost without being counted as a gap. A rollback to a
  revision that already has the spend code needs no such step.
- Move each service back to the revision recorded before the release:

  ```bash
  gcloud run services update-traffic <service> --project="$GCP_PROJECT_ID" \
    --region="$CLOUD_RUN_REGION" --to-revisions=<previous revision>=100
  ```

  for `$API_SERVICE`, `$WORKER_SERVICE`, `$BEAT_SERVICE` and `$UI_SERVICE`. The deploy script does this itself when the new API fails
  its health check.
- The spend migrations are forward-only in production. They add tables only, so
  the previous revisions run unchanged against the new schema and ignore the
  spend tables. Do not run the Alembic downgrade: it deletes the spend tables and
  everything in them.
- A revision from before the spend release does not have the `GET /audit`
  commercial filter. Once any tenant has written spend reference data, invoices
  or jobs, rolling back to such a revision lets audit readers other than
  administrators and auditors see those audit rows. Prefer
  [disabling](#4-disable) the feature on the current release and staying there;
  if a rollback past the spend release is unavoidable, disable and drain first
  as above, keep the rollback short and record the exposure in the change
  record. A rollback runs the target revision with the environment variables
  it was created with, `AGENTICORG_SPEND_AUDIT_COMMERCIAL_FILTER` included, so
  read the target revision's value before moving traffic
  (`gcloud run revisions describe <revision> --project="$GCP_PROJECT_ID"
  --region="$CLOUD_RUN_REGION" --format='value(spec.containers[0].env)'`).
  That variable, not a rollback past the spend release, is the way to restore
  the previous `GET /audit` behaviour when an owner decides to (see
  [disable](#4-disable)).

## Reference

- [AI spend intelligence feature reference](../finops/spend-intelligence.md): API, scopes, who reads what, meter and writer, jobs, reconciliation and the Gate 1 status
- [AI spend intelligence user guide](../user-guide/ai-spend-intelligence.md)
- [FinOps attribution, thresholds and forecasts](../governance/finops.md): the separate `AGENTICORG_FINOPS_*` features
- [Deployment guide](../deployment.md) and [production smoke runbook](production_smoke.md)
- Alert rules: `monitoring/prometheus/agenticorg-alerts.yml`
