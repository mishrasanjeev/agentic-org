# Production Smoke Readiness Runbook

This runbook defines a cost-capped authenticated production smoke process for the existing AgenticOrg production services. It does not provision infrastructure, change production traffic, or run paid provider checks by default.

Current reference state:

- Production app commit: `46798ad20c82ee02cdf76e362f16f92c7163c081`
- Main after smoke-tooling merge: `016da0b3c2fb0a3e42535e91bca270fae6ac620e`
- API revision at last deploy: `agenticorg-api-00064-tqc`
- UI revision at last deploy: `agenticorg-ui-00033-vvx`

## No-New-Infra Policy

Do not create new GCP infrastructure for smoke readiness unless the owner separately approves a costed proposal. This includes Cloud Run services, jobs, databases, queues, schedulers, buckets, VPC resources, Artifact Registry repositories, Pub/Sub topics, or service accounts.

The smoke script uses only existing production URLs and credentials supplied through environment variables. By default it runs public read-only health checks and reports all missing or disabled authenticated checks as `SKIPPED`.

## Cost Estimate

Expected incremental monthly GCP cost for the default smoke process is near zero. The default checks make a small number of HTTP requests to already-running production services and do not create resources.

Potential paid or usage-sensitive paths are skipped by default:

- LLM/chat checks
- RAG/vector/knowledge search checks
- content-safety checks if backed by a paid provider
- CA pack promotion checks
- workflow run creation
- signed CDC/event-wait dedupe checks that write smoke events
- connector/provider/payment/email/SMS checks

Any future provisioning proposal must include an estimated monthly cost before work starts.

## Required Environment Variables

Set the public API base URL when testing a non-default production endpoint:

- `AGENTICORG_PROD_API_BASE_URL`

Authentication requires either a bearer token or login credentials:

- `AGENTICORG_SMOKE_BEARER_TOKEN`
- `AGENTICORG_SMOKE_EMAIL`
- `AGENTICORG_SMOKE_PASSWORD`

If neither bearer token nor email/password is provided, authenticated checks are skipped.

## Optional Environment Variables

General runner controls:

- `AGENTICORG_PROD_SMOKE_TIMEOUT_SECONDS`
- `AGENTICORG_PROD_SMOKE_DRY_RUN`
- `AGENTICORG_PROD_SMOKE_ENABLE_COST_RISKY`
- `AGENTICORG_PROD_SMOKE_ENABLE_SIGNED_EVENT`

Authenticated read-only checks:

- `AGENTICORG_SMOKE_BRIDGE_ID`
- `AGENTICORG_SMOKE_WORKFLOW_RUN_ID`

Cost-risky or mutating checks, disabled unless `AGENTICORG_PROD_SMOKE_ENABLE_COST_RISKY=1`:

- `AGENTICORG_SMOKE_CA_AGENT_ID`
- `AGENTICORG_SMOKE_ZOHO_MISSING_AGENT_ID`
- `AGENTICORG_SMOKE_WORKFLOW_ID`
- `AGENTICORG_SMOKE_CHAT_QUERY`
- `AGENTICORG_SMOKE_CHAT_AGENT_ID`
- `AGENTICORG_SMOKE_KNOWLEDGE_QUERY`
- `AGENTICORG_SMOKE_CONTENT_SAFETY_TEXT`

Signed event check, disabled unless `AGENTICORG_PROD_SMOKE_ENABLE_SIGNED_EVENT=1`:

- `AGENTICORG_SMOKE_CDC_TENANT_ID`
- `AGENTICORG_SMOKE_CDC_CONNECTOR`
- `AGENTICORG_SMOKE_CDC_SECRET`
- `AGENTICORG_SMOKE_CDC_EVENT_ID`

## Future Secret Manager Names

If the owner approves managed smoke credentials later, use these exact Secret Manager names:

- `agenticorg-prod-smoke-bearer-token`
- `agenticorg-prod-smoke-email`
- `agenticorg-prod-smoke-password`
- `agenticorg-prod-smoke-tenant-id`
- `agenticorg-prod-smoke-bridge-id`
- `agenticorg-prod-smoke-workflow-run-id`
- `agenticorg-prod-smoke-cdc-tenant-id`
- `agenticorg-prod-smoke-cdc-connector`
- `agenticorg-prod-smoke-cdc-secret`

Do not store connector credentials, provider API keys, payment credentials, or customer data in smoke secrets.

## Safe Manual Execution

Default public smoke:

```powershell
python scripts/prod_smoke_check.py
```

Dry-run with authenticated credentials loaded into the environment:

```powershell
$env:AGENTICORG_PROD_SMOKE_DRY_RUN = "1"
$env:AGENTICORG_SMOKE_BEARER_TOKEN = "<from approved secret source>"
python scripts/prod_smoke_check.py --dry-run
```

Authenticated read-only smoke with a bearer token:

```powershell
$env:AGENTICORG_SMOKE_BEARER_TOKEN = "<from approved secret source>"
$env:AGENTICORG_SMOKE_WORKFLOW_RUN_ID = "<approved smoke workflow run>"
python scripts/prod_smoke_check.py
```

Signed CDC/event-wait dedupe smoke, only after the owner approves the smoke tenant and connector secret:

```powershell
$env:AGENTICORG_PROD_SMOKE_ENABLE_SIGNED_EVENT = "1"
$env:AGENTICORG_SMOKE_CDC_TENANT_ID = "<approved smoke tenant>"
$env:AGENTICORG_SMOKE_CDC_CONNECTOR = "<approved smoke connector>"
$env:AGENTICORG_SMOKE_CDC_SECRET = "<from approved secret source>"
python scripts/prod_smoke_check.py
```

Do not enable `AGENTICORG_PROD_SMOKE_ENABLE_COST_RISKY=1` unless the owner explicitly approves the specific check and accepts any provider usage cost.

## Secret Handling

The smoke script must never print secrets, tokens, cookies, auth headers, signatures, connector credentials, raw signed secrets, or raw provider credentials. Missing credentials produce explicit `SKIPPED` results with variable names only.

Operators must source secrets from the approved secret source for the current environment. Do not paste secrets into docs, PR comments, shell history, screenshots, or logs.

## Rotation and Disable Procedure

To disable smoke access immediately:

1. Revoke the smoke user's session or bearer token in the production auth system.
2. Disable or rotate the corresponding Secret Manager secret version if one exists.
3. Remove any locally exported `AGENTICORG_SMOKE_*` variables from the operator shell.
4. Run default public smoke to confirm production remains reachable without authenticated smoke credentials.

To rotate credentials:

1. Create or reset the approved smoke account credential.
2. Add a new Secret Manager version for the affected `agenticorg-prod-smoke-*` secret.
3. Disable the old secret version after confirming the new value works.
4. Run authenticated read-only smoke without enabling cost-risky checks.

## Rollback and No-Op Guidance

This runbook and `scripts/prod_smoke_check.py` are operational tooling only. Running default smoke does not deploy, migrate, or change traffic.

If smoke checks fail after a deploy:

1. Do not retry cost-risky checks until the failure mode is understood.
2. Check Cloud Run revision health and application logs.
3. Use the deployment rollback target documented in `docs/deployments/2026-05-17-post-deploy-46798ad.md` if the active revision is unhealthy.
4. If credentials are missing or disabled, treat authenticated smoke as `SKIPPED`, not as a product outage.

## Cloud Run Pinned-Traffic Deploys

Cloud Run services can retain explicit revision traffic pinning after `gcloud run services update`. In that state a new image/env update can create a new revision without sending public traffic to it. A deploy script that only polls the public health URL will keep seeing the old commit and must not report success.

The official deploy helper stages API and UI revisions with `--no-traffic`, records the previous traffic allocation, and then uses an explicit traffic mode:

- `--traffic latest` stages the target revisions, probes the new API revision through a Cloud Run tag when Cloud Run returns a tagged URL, moves API traffic to the target revision, verifies public health reports the target commit, then moves UI traffic.
- `--traffic preserve` stages target revisions only and reports `NOT DEPLOYED`.
- `--traffic manual` stages target revisions only, reports `NOT DEPLOYED`, and prints the exact `gcloud run services update-traffic` commands an operator can run after separate approval.

If API health fails after a traffic shift, the script rolls API and UI traffic back to the previously captured allocation. If UI traffic movement fails after API verification, the script also rolls back both services. This prevents a new UI revision from remaining live against an old or unhealthy API revision.

Dry-run mode prints the previous traffic allocation and the planned traffic changes without updating services or traffic.

### No-Traffic Revision Readiness

Cloud Run revisions staged with `--no-traffic` may report `Ready=True` on the revision object while the service-level `status.latestReadyRevisionName` remains pinned to the currently serving revision. The deploy helper must therefore check readiness on the specific staged revision object, not on the service-level latest-ready field.

This was observed during the attempted deploy of commit `2d3a9ac6eb2249fe6debe915f9c521692a8b9f75`: API revision `agenticorg-api-00067-fpj` was created with `Ready=True` and no public traffic, while `agenticorg-api` still reported `latestReadyRevisionName=agenticorg-api-00065-zp4`. The script now treats the staged revision as ready only when the revision object has `Ready=True`, belongs to the expected service, and its image plus commit metadata match the requested deploy SHA.
