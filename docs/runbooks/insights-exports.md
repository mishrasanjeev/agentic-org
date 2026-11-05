# Insights background exports

Background exports (`POST /api/v1/insights/exports`) make files the worker builds: explorer tables and metrics
larger than an inline export (up to 500,000 rows), run evidence bundles, and (with the packs) view PDFs, the
finance pack and period evidence packs. Files are kept for seven days and listed on the Downloads page
(`/dashboard/insights/downloads`). Downloads go through the API with one signed `insights.export` audit row;
no signed URL is ever issued.

## Set up

1. Create the bucket in the platform project, in the same region as the services:

   ```bash
   gcloud storage buckets create gs://agenticorg-insights-exports --project=perfect-period-305406 \
     --location=asia-southeast1 --uniform-bucket-level-access --public-access-prevention
   ```

2. Delete objects after eight days (the app deletes them after seven; the rule is the backstop):

   ```bash
   echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":8}}]}' > lifecycle.json
   gcloud storage buckets update gs://agenticorg-insights-exports --lifecycle-file=lifecycle.json
   ```

3. Grant the API and worker service accounts `roles/storage.objectAdmin` on that bucket only.
4. Set `AGENTICORG_INSIGHTS_EXPORT_BUCKET=agenticorg-insights-exports` on the API, worker and beat services.

Without the setting, `POST /insights/exports` answers 503 `export_storage_unconfigured` and the Downloads page
says background files are not set up; inline exports (up to 50,000 rows) keep working.

## Operations

- Builds run on the `reports` queue (`core.tasks.insights_tasks.build_export`). A job still queued or running two
  hours after it was made is marked failed (`build_lost`) by the daily sweep.
- `insights-expire-exports` (beat, 03:15 UTC) deletes files past `expires_at` for every tenant, Insights on or off,
  and marks the jobs expired.
- PII: when the tenant's governance setting `pii_masking` is on (the default), exports mask emails, phone numbers,
  Aadhaar, PAN, bank account and IFSC patterns. Administrators and auditors may ask for raw values; the job and the
  audit row record it.
- Rollback: the routes and tasks are additive; migration v6z110 only adds `insights_exports`.

## Packs and sign-off

- **View PDF** (`view_pdf`, any view the caller may read): tiles as a table (value, state, change, data quality),
  then each chart as its data with a bar per row; empty figures carry their hint. The core PDF fonts are Latin-1:
  names in other scripts print with `?` in place of the characters they cannot draw.
- **Finance pack** (`finance_pack`, administrators and the CFO): `finance.xlsx` (Summary, Spend by day, Spend by
  agent, Spend by model, Budgets, Tokens, Money saved, Assumptions, Filters, Definitions), `finance.pdf` and
  `manifest.json`.
- **Period evidence pack** (`evidence_pack`, administrators and auditors, at most 366 days, at most 200,000 audit
  rows): `summary.pdf`, `audit_log.csv`, `approvals.csv`, `delegations.csv`, `dsar.csv` (no subject),
  `residency_attestations.csv`, `change_log.csv`, `verification.json` and `manifest.json`.
  - `verification.json` checks the HMAC of every audit row in the period and, where rows are sealed into the audit
    chain, re-verifies the chain over the sequence range that covers the period (from the link before it). It also
    lists the anchor and witness checkpoints. Its `status` is `verified`, `signatures_verified` (no sealed rows in
    the period) or `broken`, and `failed_rows` names any row whose signature no longer matches.
  - `manifest.json` lists each file's SHA-256 and size and carries an HMAC-SHA256 of itself with the platform audit
    key (`key_id` `platform-audit-key`). An auditor checks the files with `sha256sum` against the manifest; the
    HMAC is checked by the platform (it needs the key).
- **Sign-off**: on the Downloads page an auditor or administrator signs a ready evidence pack once, with a
  statement. The signature (who, role, statement, time, the pack's and the manifest's SHA-256, the verification
  status) is stored on the job and in a signed `insights.evidence_pack.signed` audit row. This is the Phase 3 gate's
  "auditor signs a period evidence pack".
- **Chart pictures**: every chart's export menu also saves the chart as SVG or PNG, with the title, period, filters
  and data quality drawn above it (made in the browser; nothing is sent to the server).

## Scheduled Insights reports

- The Report Scheduler (`/dashboard/report-schedules`) has three Insights types: `insights_view` (a view as a PDF),
  `insights_finance_pack` and `insights_evidence_pack`. Each keeps a period preset (`last_7_days`, `last_30_days`,
  `last_week`, `last_month`, `month_to_date`) counted in the tenant's time zone. "Schedule weekly" on an Insights view
  opens the form for that view, every Monday at 09:00, for last week.
- Who may schedule what: administrators any type; the CFO the Finance view and the finance pack; the CFO, CHRO, CMO
  and COO their own view. Evidence pack schedules are made by administrators: auditors have no report-schedule
  scopes.
- A run builds the file as the schedule's owner (`report_schedules.created_by`, migration v6z111), with that user's
  access read at run time; older schedules without an owner run as the tenant's first active administrator. If the
  owner is no longer active the run fails (`no_owner`); if they may no longer read the view it is blocked.
- Delivery: email recipients get a link to Downloads, never the file. They download it after signing in, and only
  if their email address is a user of the organisation with Insights access (`shared_with` on the export).
  Slack and WhatsApp get the file. A view whose every figure has no data for the period is not sent (`blocked`,
  `no_data`).
- Every run (Insights and the older report types, scheduled or "Run now") is recorded in `report_schedule_runs`; the
  scheduler's History button lists the last 20 with why a run failed or was held back.
- Cron strings are now evaluated by `core.reports.cron` in the tenant's time zone. Before this, `croniter` was not
  installed and every cron schedule ran daily. The preset keywords still mean "this long after the last run".
