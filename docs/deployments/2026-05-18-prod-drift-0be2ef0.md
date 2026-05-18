# 2026-05-18 Production Drift Provenance: `0be2ef0`

Production drift was detected before authenticated smoke testing. No deploy,
traffic, credential, or infrastructure changes were made during this
investigation.

## Summary

- Previously recorded production deploy:
  - Commit: `46798ad20c82ee02cdf76e362f16f92c7163c081`
  - API revision: `agenticorg-api-00064-tqc`
  - UI revision: `agenticorg-ui-00033-vvx`
- Observed production state:
  - Commit: `0be2ef0a384311967ce875d1ea5128345fefb457`
  - API revision: `agenticorg-api-00065-zp4`
  - UI revision: `agenticorg-ui-00034-zck`
  - API traffic: 100% to `agenticorg-api-00065-zp4`
  - UI traffic: 100% to `agenticorg-ui-00034-zck`

## Timeline

- `2026-05-17T10:44:58Z`: PR #597 merged as
  `46798ad20c82ee02cdf76e362f16f92c7163c081`.
- `2026-05-17T11:08:20Z`: API revision `agenticorg-api-00064-tqc`
  created with `AGENTICORG_GIT_SHA=46798ad...`.
- `2026-05-17T11:08:27Z`: UI revision `agenticorg-ui-00033-vvx`
  created. Its `GIT_SHA` env still showed an older value, documented in the
  post-deploy note.
- `2026-05-17T13:02:50Z`: PR #599 merged as
  `0be2ef0a384311967ce875d1ea5128345fefb457`.
- `2026-05-17T13:30:42Z` to `2026-05-17T13:44:47Z`: Cloud Build built and
  pushed the API image tagged `0be2ef0...`.
- `2026-05-17T13:45:36Z` to `2026-05-17T13:47:57Z`: Cloud Build built and
  pushed the UI image tagged `0be2ef0...`.
- `2026-05-17T13:48:15Z`: API revision `agenticorg-api-00065-zp4`
  created.
- `2026-05-17T13:51:40Z`: UI revision `agenticorg-ui-00034-zck`
  created.
- `2026-05-18T03:05:37Z`: Public and direct health checks both reported
  commit `0be2ef0...`.

## GitHub Evidence

PR #599:

- URL: `https://github.com/mishrasanjeev/agentic-org/pull/599`
- Title: `fix(commerce): gate public commerce discovery`
- Merge commit: `0be2ef0a384311967ce875d1ea5128345fefb457`
- Merged: `2026-05-17T13:02:50Z`
- Changed files:
  - `api/v1/a2a.py`
  - `api/v1/mcp.py`
  - `core/commerce/discovery_gate.py`
  - `docs/commerce-agent-c3-hosted-smoke-runbook.md`
  - `docs/commerce-agent-developer-guide.md`
  - `docs/commerce-agent-overview.md`
  - `docs/route_inventory.json`
  - `tests/regression/test_commerce_sales_agent_no_provider_calls.py`
  - `tests/unit/test_a2a_mcp.py`

PR checks passed for lint, unit tests, integration tests, security scan, CodeQL,
and RAG quality gate. On the post-merge main run for `0be2ef0`, lint, unit
tests, integration tests, security scan, and release acceptance completed
successfully, but the `build` job was cancelled while building the API image.
The downstream `approval-gate`, `deploy-production`, `deploy-staging`,
`e2e-tests`, and `submit-indexing` jobs were cancelled. Therefore the later
production images were not produced by the completed GitHub Actions build job.

## Cloud Run Evidence

Current API service state:

- Service: `agenticorg-api`
- Current traffic: 100% to `agenticorg-api-00065-zp4`
- Revision created: `2026-05-17T13:48:15.994152Z`
- Deployment client: `gcloud` / client version `568.0.0`
- Image tag configured on service:
  `asia-south1-docker.pkg.dev/perfect-period-305406/agenticorg/agenticorg:0be2ef0a384311967ce875d1ea5128345fefb457`
- Imported revision image digest:
  `asia-south1-docker.pkg.dev/perfect-period-305406/agenticorg/agenticorg@sha256:7ce86f5f123dd5326705a4a09a85555ea4f2259da16a4d636f1b0b0f31dc882a`
- Commit env metadata:
  `AGENTICORG_GIT_SHA=0be2ef0a384311967ce875d1ea5128345fefb457`

Current UI service state:

- Service: `agenticorg-ui`
- Current traffic: 100% to `agenticorg-ui-00034-zck`
- Revision created: `2026-05-17T13:51:40.480331Z`
- Deployment client: `gcloud` / client version `568.0.0`
- Image tag configured on service:
  `asia-south1-docker.pkg.dev/perfect-period-305406/agenticorg/agenticorg-ui-cloudrun:0be2ef0a384311967ce875d1ea5128345fefb457`
- Imported revision image digest:
  `asia-south1-docker.pkg.dev/perfect-period-305406/agenticorg/agenticorg-ui-cloudrun@sha256:87f6f3888f594b194e48de23679c1ccfcc46808ce02c157987bd23e972244783`
- Commit env metadata:
  `GIT_SHA=0be2ef0a384311967ce875d1ea5128345fefb457`

The previous API revision `agenticorg-api-00064-tqc` is retired and had
`AGENTICORG_GIT_SHA=46798ad...`. The previous UI revision
`agenticorg-ui-00033-vvx` is also retired.

## Artifact Registry Evidence

API image for `0be2ef0...`:

- Tag:
  `asia-south1-docker.pkg.dev/perfect-period-305406/agenticorg/agenticorg:0be2ef0a384311967ce875d1ea5128345fefb457`
- Digest:
  `sha256:7ce86f5f123dd5326705a4a09a85555ea4f2259da16a4d636f1b0b0f31dc882a`
- Artifact create/update time: `2026-05-17T13:44:45.876671Z`
- Cloud Build:
  `https://console.cloud.google.com/cloud-build/builds/136806a5-4afd-45b7-892c-2d1d849c6320?project=490751771290`

UI image for `0be2ef0...`:

- Tag:
  `asia-south1-docker.pkg.dev/perfect-period-305406/agenticorg/agenticorg-ui-cloudrun:0be2ef0a384311967ce875d1ea5128345fefb457`
- Digest:
  `sha256:87f6f3888f594b194e48de23679c1ccfcc46808ce02c157987bd23e972244783`
- Artifact create/update time: `2026-05-17T13:47:56.601311Z`
- Cloud Build:
  `https://console.cloud.google.com/cloud-build/builds/9279e1b1-7ee0-4c65-a215-d9d7387ea198?project=490751771290`

Both Artifact Registry records have `slsa_build_level: unknown`; the available
evidence points to a Cloud Build/manual gcloud path rather than the completed
GitHub Actions build job for the `0be2ef0` main run.

## Runtime Health Evidence

Sampled at `2026-05-18T03:05:37Z`:

- `https://app.agenticorg.ai/api/v1/health`: HTTP 200,
  `{"status":"healthy","version":"4.8.0","commit":"0be2ef0a384311967ce875d1ea5128345fefb457","checks":{"db":"healthy","redis":"healthy"}}`
- `https://agenticorg-api-490751771290.asia-southeast1.run.app/api/v1/health`:
  HTTP 200 with the same commit and dependency checks.
- `https://app.agenticorg.ai/api/v1/health/liveness`: HTTP 200,
  `{"status":"alive"}`
- `https://app.agenticorg.ai/api/v1/product-facts`: HTTP 200,
  `{"version":"4.8.0","connector_count":54,"agent_count":26,"tool_count":330}`

The app-domain and direct Cloud Run API health endpoints agree, so the smoke
script did not hit the wrong API base URL.

## Current Main Invariants

Validated on current `origin/main` at
`ecf7ff1694be3f9b4b8bdd4670514d1d4c51b982`:

- `routes_missing_metadata: 0`
- `process_local_allowed: 0`
- `process_local_blocked: 0`
- `broad_exceptions_allowed: 22`
- `total_blocked: 0`
- Alembic head: `v4916_merge_p0_heads`

Note: `origin/main` has advanced beyond the current production commit via PR
#600. Production is on `0be2ef0...`, not latest main.

## Risk Assessment

PR #599 is runtime-impacting but narrowly scoped. It changes public A2A and MCP
discovery output by hiding the commerce sales agent unless
`AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED` is explicitly set to a safe true
value. It adds no database migration and does not change authenticated A2A/MCP
execution, tenant resolution, connector execution, billing, workflow, or
credential paths.

Known validation:

- PR CI passed before merge.
- Post-merge main run for `0be2ef0` passed lint, unit tests, integration tests,
  security scan, and release acceptance.
- Post-merge main build/deploy did not complete; the run was cancelled during
  the API image build.
- The deployed images were created later through Cloud Build/gcloud, not through
  a completed GitHub Actions build/deploy run.
- No migration was included in PR #599, so no matching production migration job
  is expected for this commit.

Risk conclusion: the code change itself appears low-to-moderate risk and
fail-closed for public commerce discovery. The release provenance risk is higher
because the deployed image path did not correspond to a fully completed main
CI build/deploy run.

## Decision Matrix

### Option A: Accept `0be2ef0` as Current Production Target

Conditions:

- Owner confirms the `0be2ef0` deploy was intentional or acceptable.
- Owner accepts the validation gap that the main GitHub Actions build/deploy
  run for `0be2ef0` was cancelled.
- Run public smoke again against `0be2ef0`.
- Proceed with authenticated smoke only after approved smoke credentials are
  provided.

Pros:

- Matches the currently serving API and UI revisions.
- Avoids another production change.
- The code change is narrow and fail-closed for public discovery.

Risks:

- Current production image was not proven to come from a completed GitHub
  Actions build job.
- Current production is behind latest main (`ecf7ff...`).

### Option B: Redeploy Latest Verified Main Through Official Path

Conditions:

- Identify the latest desired main commit.
- Require full main CI green, including release acceptance and build.
- Use only the official Cloud Run deploy script/path.
- Run Alembic migration job only if required by the chosen commit.
- Run public smoke after deploy; authenticated smoke remains credential-gated.

Pros:

- Restores clean release provenance through the official path.
- Moves production to the latest accepted main state.

Risks:

- Requires a production deploy.
- If latest main includes only docs/runbook changes, the runtime benefit may be
  limited relative to the deployment risk.

### Option C: Roll Back to Previous Known-Good Revision

Conditions:

- Verify previous revisions are still available.
- Approve a traffic-only rollback to known-good revisions if needed.
- Run public smoke after rollback.

Pros:

- Fastest way to restore the previously recorded production target.
- Avoids building new images.

Risks:

- Reverts the commerce discovery gate from `0be2ef0`.
- Requires production traffic change.
- Authenticated smoke still remains blocked without approved credentials.

## Recommendation

Pause authenticated smoke until the owner chooses one path:

1. Accept `0be2ef0` as the production target and proceed with smoke when
   credentials are available.
2. Approve redeploying latest verified main through the official path.
3. Approve rollback to the previous known-good revisions.

No deploy, traffic, credential, or infrastructure changes were made while
creating this report.
