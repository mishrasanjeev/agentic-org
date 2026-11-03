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
