# Production Smoke Readiness Runbook

This runbook defines a cost-capped authenticated production smoke process for the existing AgenticOrg production services. It does not provision infrastructure, change production traffic, or run paid provider checks by default.

## Coordinated Dependency Releases

Validate provider integrations and their `langchain-core` floor as one resolved
set in both project metadata and pinned requirements. Graph, checkpoint and
Postgres-checkpoint updates also require reviewing the sealed saver's overridden
methods and the upstream migration list. Keep exact-version refusal enabled;
replay encrypted persistence, pending-write ordering, process restart/resume,
tampering refusal and tenant isolation against local Postgres before rollout.
An unchanged migration list needs no new schema revision. A passing individual
dependency PR does not replace combined-release tests.

Capture current deployment controls before rollout and compare them afterwards.
Do not enable the optional explanation-history reader or change paused-agent,
runtime, budget, payment or discovery controls as part of a dependency release.

Historical reference state (not a current production inventory):

- Production app commit: `46798ad20c82ee02cdf76e362f16f92c7163c081`
- Main after smoke-tooling merge: `016da0b3c2fb0a3e42535e91bca270fae6ac620e`
- API revision at last deploy: `agenticorg-api-00064-tqc`
- UI revision at last deploy: `agenticorg-ui-00033-vvx`

## Worker Startup And Rollback Evidence

Before a rollout, resolve the current API/UI/worker/beat traffic revisions and
verify their exact image digests still exist in Artifact Registry. A saved
revision name is not a usable rollback if image cleanup deleted its digest.
Do not delete or overwrite release images during validation.

After a rollout, check the worker revision's Celery-ready log, expected queue
registration and successful task completion, not only Cloud Run's Ready flag.
The worker's health listener now starts on Celery's `worker_ready` signal after
metrics initialization, vault validation, imports and initial broker connection.
It is a startup check, not a continuous broker or end-to-end connector check.
Beat uses a single-process metrics registry; verify scheduler startup separately.

The October 2026 MCP rollout exposed late metrics initialization through vault
imports. Setting the existing worker's environment to production restored task
processing under the existing nonfatal production metrics policy; that was
recovery, not a substitute for the import-order fix. Never weaken vault checks
or rely on an early HTTP listener to hide a startup exception.

## Knowledge Indexing Acceptance

Use a dedicated synthetic workspace and compare extraction, document status,
retrieval and excerpt separately. Search returns chunk citation IDs; use that
`citation.document_id` for the excerpt, not the upload's metadata ID. Source
filters require the exact `citation.source`, including its chunk suffix.

Replay native TXT/CSV/PDF/DOCX/XLSX/PPTX and real PNG/scanned-PDF OCR in the
runtime image, not an image lacking the OCR executables. Check short content,
provenance, malformed/password-protected input, failed replacement preservation,
successful old-chunk retirement, deletion and a second-tenant refusal.

For an explicitly approved recovery rollout, set
`DEPLOY_KNOWLEDGE_UPLOAD_RECOVERY=true` on the migration-first deployment helper.
It applies the default-off runtime flag consistently to API, worker and beat.
Unset preserves deployed settings; `false` stops new retries. Roll back to a
release containing the same recovery contract or disable the flag deliberately;
do not silently strand processing rows under an older worker.

The existing maintenance queue checks pending native uploads every minute,
at most ten due rows per tenant per sweep. Each document has one initial attempt
and at most four transient retries. A row lock covers native indexing,
replacement and final status. Pending extracted spans are bounded to 4 MiB and
10,000 spans, contain no raw upload file, and are removed on success, permanent
failure or deletion. Authorization/input errors do not retry. Recovery does not
change embedding model, vector dimension or request timeout. Verify the actual
production serving profile, especially 1024-dimensional multilingual deployments;
a 384-dimensional local fixture is not proof of that live profile.

Cold-service timeout tests belong in local Docker with a controlled provider.
Do not stop production embeddings to manufacture a failure. After deployment,
verify the flag on all three services, worker task registration and real
upload/search/excerpt success; list untested cold-start paths explicitly.

The hosted embedding service scales to zero. When it is cold (a cold start
took about 80 s on 2026-10-20) the first upload after idle is accepted as
`processing` with "Retry pending" and is indexed by the worker within about
three minutes; that is the recovery path working, not a failure. The post-deploy
browser test `ui/e2e/knowledge-upload-recovery.spec.ts` waits for it within
`KNOWLEDGE_INDEX_BUDGET_MS` (`ui/e2e/helpers/knowledge.ts`), which
`tests/regression/test_knowledge_upload_e2e_budget_20261021.py` keeps in step
with the retry delays, the beat period and the embedding read timeout. A row
that ends `failed`, or stays `processing` past that budget, is a real failure.

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

## Bounded Real Reasoning Acceptance

Obtain a total spending allowance and an owner-controlled workspace before a
paid test. Use a personal shadow agent with no tools, connectors, voice, output
repair schema or previous conversation. Pin an available model, disable routing
and set agent retries to zero. Bound the input, model output and provider SDK
transport retries. Include the separate explanation-generation request in the
allowance; a zero retry setting is not a promise of one provider request.

Use synthetic arithmetic with an independently checked answer. First confirm a
review trigger is respected. Then request valid JSON with numeric `answer` and
`confidence` and inspect the recorded status and output without lowering the
review threshold. Record token counts and model-price estimates separately from
invoice-confirmed charges; a UI rounded to cents is not evidence of a free run.
Do not rerun just to produce a screenshot. Preserve an existing successful run
for read-only post-deploy verification, then remove only disposable test data.

Treat incorrect arithmetic as a failed acceptance even when the run completes
with high confidence. Replay the exact task through the local production-image
PII detector before retrying the paid call. A detector can mistake a bare count
for `DATE_TIME`; the quantity regression exercises the real runner message and
keeps dates, contact details and overlapping identifiers protected. Do not
disable redaction, change review thresholds, or accept a different answer to
make the check green. A local detector fix is not a successful production
reasoning replay: record those two outcomes separately.

For `gemini-3.5-flash-lite`, opt in the synthetic acceptance agent only through
**Config > Edit > Gemini thinking > Medium** (or `llm.thinking_level="medium"`
in its authenticated agent configuration). Both runtime clients then request
`thinking_config.thinking_level=medium`. Existing agents, generic calls and new
agents without an explicit profile keep the provider default. Set the field to
`null` to restore that default. No global flag or environment change is required.
Other models reject this profile rather than silently applying or ignoring it.
Saved profiles also travel through workflow agent loading, approval/debug resume,
legacy sales-agent execution and the text-to-agent voice bridge. Generic chat,
type-only A2A/MCP calls and agents with no saved profile keep their old defaults.
Voice bridge regression tests do not place a call or certify a live voice account.
The provider defaults to minimal
thinking; its temperature/top-p/top-k parameters are ignored. Keep the exact
system prompt, assembled task, redaction and selected model fixed when isolating
reasoning settings. Do not infer that a transport adapter rewrote the answer
unless the captured model boundary proves it. The installed adapter's actual
request serializer is covered by regression tests, not just a constructor mock.
See Google's [model guidance](https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.6).

Thinking tokens are billed at the output rate. Native router totals and estimated
cost include them even when optional spend intelligence is off; when on, its
detailed usage keeps candidate output and thinking separate to avoid double
counting. LangGraph uses the adapter's output usage, which includes reasoning.
Reserve for the automatic explanation and transport retries as well. Medium
thinking can increase latency and cost, and model-reported confidence remains
unverified. Never substitute a scripted response for real-provider acceptance.

The direct-run explanation history is an independent read-only opt-in:
`AGENTICORG_AGENT_EXPLANATION_AUDIT_ENABLED` defaults to false. The Cloud Run
helper accepts `DEPLOY_AGENT_EXPLANATION_AUDIT=true` or `false` for the API only;
omitting it preserves the installed value. It reads existing indexed audit rows
after the same tenant and agent visibility checks, performs no model call and
does not rewrite history. Legacy records have unknown tool-call details and
review decisions must be checked in Approvals. Disable the flag to restore the
previous read behavior. Do not change paused-agent, runtime, tenant-budget,
public-discovery or provider-execution controls as part of this smoke test.

### Reuse the Existing Telegram Connection

Record the original connection's enabled state, destination and callback before
the test. A bot accepts one webhook. `telegram_bot_connected_elsewhere` on a new
connection is the expected refusal when the old callback is still registered;
it does not mean the original integration is unhealthy. Reuse the original
connection. Do not rotate its credentials, overwrite its webhook, delete that
webhook or use polling to bypass this control. Leave an unused duplicate disabled.

Use one uniquely labeled private message from an approved, linked sender. Match
the inbox delivery and run result, then confirm the status notice in Telegram.
A workflow that copies text is transport evidence only. Record an independent
real Gemini result separately; claim a combined path only when that exact channel
delivery invokes a model. Status-only remains the default. For an explicitly
approved answer-reply test, record the original destination and reply settings,
select the tool-free test agent and `reply_mode=agent_answer` in Channels, and
enable Send replies. Ask one complete synthetic question from the linked private
account; verify the same inbox run and the actual answer received in Telegram.
Restore the recorded settings unless the owner approved keeping the new route.
Do not change flags, credentials, webhook registration or tenant limits. Verify
that approval-pending, unlinked, revoked and secret-bearing results do not export
an answer locally before this account-backed test. Legacy queued messages must
stay status-only. Apply the additive reply-mode migration before new workers.

Retain sanitized timestamps, model, outcome, token counts and cost estimates, not
credentials, link commands, sender identifiers, tenant identifiers or raw provider
payloads. Include auxiliary explanation requests and SDK retries in the approved
spending bound. Do not enable an audit reader, spend instrumentation or any other
feature flag merely to collect this evidence. Source an unchanged deployment
baseline before and after the test and preserve all existing controls.

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

### Embedded Python Quoting

During the attempted deploy of commit `83fa3ac56e3765e273b3eb2e07bed0cbc388d41a`, the deploy helper staged API revision `agenticorg-api-00068-d4q` with the correct image digest and `AGENTICORG_GIT_SHA`, but failed before moving traffic. Traffic remained unchanged at `agenticorg-api-00065-zp4` and `agenticorg-ui-00034-zck`.

Root cause: the non-dry-run revision readiness path used multiline `python3 -c '...'` snippets containing Python string literals such as `'<unknown>'`. Bash closed the single-quoted shell string early and treated `<unknown>` as redirection. Dry-run did not catch the issue because it did not execute the revision readiness parser.

The deploy helper now uses quoted Python here-docs and a resolved `PYTHON_BIN` interpreter for embedded parsing. This keeps Python string literals opaque to Bash and works under both normal Linux bash and Git Bash on Windows.
