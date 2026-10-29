# Production run

`scripts/onboarding_desk_production_run.py` proves the desk works in a production deployment, using
the live public API and a tenant it creates for the purpose. It covers the whole flow except the
checks. Everything it uses is synthetic, and it cannot send a message.

## What it does

| Step | What is checked |
| --- | --- |
| Disposable tenant | A new tenant named `Onboarding desk production run <time> UTC (disposable, synthetic data)`, slug `desk-production-run-<date>-<hex>`, `settings.disposable = true`. Its branding has no message senders, `auto_contact` and `sms_conversation` are off, and case settings `auto_open` and `investigate` are off. It gets one webhook lead source. |
| Signed lead with consent | An unsigned post to the webhook is refused with 401. The signed post creates the lead with its SMS and email consents (`web_form`, granted). Posting it again finds the existing lead. |
| Contact rules | The SMS, email and WhatsApp decisions are computed (WhatsApp: `no_consent`). These are decisions only: with no sender configured the desk has nothing to send with. |
| Application | A signed link opens the applicant's session through the public API. A synthetic certificate is uploaded and read, and its legal name, corporation number and Business Number are confirmed. A US postal code for an Ontario address is refused. The remaining answers are saved and the application is submitted. |
| No case | The application is submitted and no governed case is opened. |
| Evidence | The pack is built and verifies against the key the API publishes at `GET /onboarding-desk/evidence-key`, both as built and after the JSON round trip the API serves. |
| Report | The partner report counts one lead and one submitted application, with full consent coverage. No contact attempt was recorded. |
| Retirement | The webhook source is switched off, after which a signed lead is refused with 401. The tenant is soft-deleted (`deleted_at`, `settings.retired_at`). The rows stay for the audit trail. Pass `--keep` to skip this step. |

The output is one JSON summary with each step's result, the tenant id and slug, the application id,
the evidence key id and the report funnel. It holds no secret, token or contact value. The script
exits 0 only when every step passed. If a step fails, the tenant is still retired.

## Why the checks are not part of it

A production runtime refuses the mock verification provider and the example case policies by design.
The checks also need the verification partner's production credential. The full flow including the
checks, the human decision on the Grantex approval page and the signed case push runs in CI instead,
as `make e2e-onboarding-desk` (see [quickstart-local](../quickstart-local.md)).

## Running it

Run it as a one-off Cloud Run job on the API image of the deployed commit. Give the job the API's
database URL, secret key (the link tokens and contact hashes must match the API's), vault keyring
(contact details are encrypted under the tenant key) and evidence signing key. Pass no other secrets.

```bash
gcloud run jobs create agenticorg-onboarding-desk-production-run \
  --project=perfect-period-305406 --region=asia-southeast1 \
  --image=asia-south1-docker.pkg.dev/perfect-period-305406/agenticorg/agenticorg:<sha> \
  --service-account=agenticorg-cloudrun@perfect-period-305406.iam.gserviceaccount.com \
  --set-cloudsql-instances=perfect-period-305406:asia-southeast1:agenticorg-db-sg \
  --vpc-connector=agenticorg-conn --vpc-egress=private-ranges-only \
  --max-retries=0 --task-timeout=600 \
  --set-env-vars=AGENTICORG_ENV=production,AGENTICORG_ONBOARDING_DESK_ENABLED=true \
  --set-secrets=AGENTICORG_DB_URL=agenticorg-db-url:latest,AGENTICORG_SECRET_KEY=agenticorg-secret-key:latest,AGENTICORG_VAULT_KEYRING=agenticorg-vault-keyring:latest,AGENTICORG_REDIS_URL=agenticorg-redis-url:latest,AGENTICORG_ONBOARDING_DESK_EVIDENCE_SIGNING_KEY=agenticorg-onboarding-desk-evidence-signing-key:latest \
  --command=python \
  --args=-m,scripts.onboarding_desk_production_run,--base-url,https://app.agenticorg.ai,--confirm,onboarding-desk-production-run
gcloud run jobs execute agenticorg-onboarding-desk-production-run \
  --project=perfect-period-305406 --region=asia-southeast1 --wait
```

The summary is in the execution's logs. Do not use `--max-retries` above 0: each attempt creates its
own tenant.
