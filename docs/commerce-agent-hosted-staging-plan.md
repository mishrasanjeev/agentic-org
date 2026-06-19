# AgenticOrg Commerce Sales Agent Hosted Staging Plan

Status: historical M9 staging artifact; superseded by the current OACP runtime path in docs/oacp-end-to-end-flow.md. Do not deploy from this historical document.

This plan defines how AgenticOrg should run the Commerce Sales Agent against Grantex Commerce V1 hosted staging. Commerce must go through Grantex APIs and MCP tools only. It must not call Stripe, Plural, Pine, or provider credential paths directly.

## Hard Boundaries

- Do not deploy, merge, or create cloud resources during M9.
- Do not change production config.
- Do not enable production Commerce V1.
- Do not enable live payments.
- Do not enable live Plural.
- Do not edit production secrets.
- Do not commit `.tmp`, synthetic env files, bearer tokens, passports, idempotency keys, provider credentials, or secrets.
- No production database or Redis.
- Commerce Sales Agent staging is sandbox only.
- No direct Stripe/Plural/Pine/provider credential path for commerce.

## Recommended Topology

| Component | Recommended staging target | Notes |
| --- | --- | --- |
| API | Cloud Run service `agenticorg-api-staging` | Exposes health, A2A, MCP, and agent APIs. |
| UI | Cloud Run service `agenticorg-ui-staging` | Serves staging UI at the staging domain. |
| Worker | Cloud Run service `agenticorg-worker-staging` | Runs background jobs needed by staging. |
| Beat | Cloud Run service `agenticorg-beat-staging` | Runs scheduled jobs needed by staging. |
| Domain | `staging.agenticorg.ai` | Points only to staging services. |
| Grantex commerce endpoint | `https://api-staging.grantex.dev` | Used by both `GRANTEX_COMMERCE_BASE_URL` and `GRANTEX_BASE_URL`. |
| Commerce discovery | Staging only for `commerce_sales_agent` | Production discovery remains unchanged. |
| Payment/provider access | Grantex only | No direct Stripe, Plural, Pine, or provider credential path for commerce. |

The existing production helper defaults in `scripts/deploy_cloud_run.sh` point at production-shaped service names and production domains. M10 implementation should add separate staging workflow/script entry points rather than changing production helper defaults.

## Exact Non-Secret Environment Variables

Set these on AgenticOrg staging services only:

```bash
AGENTICORG_BASE_URL=https://staging.agenticorg.ai
GRANTEX_COMMERCE_BASE_URL=https://api-staging.grantex.dev
GRANTEX_BASE_URL=https://api-staging.grantex.dev
PLURAL_ENV=sandbox
```

Commerce discovery:

- Enable staging commerce discovery for `commerce_sales_agent` only.
- Ensure the Grantex connector resolves to `https://api-staging.grantex.dev`.
- Ensure default commerce tools remain `grantex_commerce:*`.
- Keep `PLURAL_ENV=sandbox` only for non-commerce app areas that already need Plural sandbox behavior.
- Commerce must not read or use direct Stripe, Plural, Pine, or provider credentials.

## Required Secrets By Name Only

Create staging-only secret values with these names or documented repo-equivalent names:

- `AGENTICORG_SECRET_KEY`
- `AGENTICORG_DATABASE_URL` or repo-equivalent database secret
- `AGENTICORG_REDIS_URL`
- `GRANTEX_COMMERCE_BEARER_TOKEN` or `GRANTEX_AGENT_ASSERTION` or `GRANTEX_API_KEY`
- LLM provider keys required by staging runtime
- Existing non-commerce provider secrets only if other staging app areas need them; commerce must not use them

Secret rules:

- Use staging-only values and staging-only IAM bindings.
- Do not copy production secret versions.
- Do not place secret values in docs, `.env`, workflow YAML, logs, PR comments, or local report files.
- Do not commit local `.tmp` evidence or synthetic env files.

## CI/CD Plan

Create manual `workflow_dispatch` staging deploy workflows only after this plan is approved. Do not reactivate disabled production or GKE deploy paths as part of staging.

Recommended AgenticOrg workflows:

- `deploy-api-staging.yml`: build and deploy `agenticorg-api-staging`.
- `deploy-ui-staging.yml`: build and deploy `agenticorg-ui-staging`.
- `deploy-worker-staging.yml`: build and deploy `agenticorg-worker-staging`.
- `deploy-beat-staging.yml`: build and deploy `agenticorg-beat-staging`.

GitHub environment:

- Environment name: `staging`.
- Required reviewers: at least one repository maintainer.
- Branch/source restriction: allow `main`, approved release branches, and explicitly named staging branches only.
- Environment secrets: staging-only GCP workload identity/provider/service account and staging-only app secrets.
- Require manual `workflow_dispatch`; do not deploy staging from arbitrary pushes initially.

No-production guardrails:

- Reject production service names `agenticorg-api`, `agenticorg-ui`, production worker, or production beat from staging workflows.
- Reject `AGENTICORG_BASE_URL=https://app.agenticorg.ai`.
- Reject `GRANTEX_COMMERCE_BASE_URL=https://api.grantex.dev`.
- Reject `GRANTEX_BASE_URL=https://api.grantex.dev`.
- Reject commerce tool configs that include direct Stripe, Plural, Pine, or provider credential paths.
- Reject staging deploys that read production GitHub environment secrets.

Post-deploy smoke checks:

- `GET https://staging.agenticorg.ai/api/v1/health`.
- AgenticOrg MCP discovery.
- AgenticOrg A2A discovery.
- Commerce Sales Agent discovery shows staging-only Grantex commerce tools.
- AgenticOrg demo/eval uses `https://api-staging.grantex.dev`.

Rollback commands:

```bash
gcloud run revisions list --service agenticorg-api-staging --region <region> --project <staging-project>
gcloud run services update-traffic agenticorg-api-staging --to-revisions <previous-revision>=100 --region <region> --project <staging-project>
gcloud run services update-traffic agenticorg-ui-staging --to-revisions <previous-revision>=100 --region <region> --project <staging-project>
gcloud run services update-traffic agenticorg-worker-staging --to-revisions <previous-revision>=100 --region <region> --project <staging-project>
gcloud run services update-traffic agenticorg-beat-staging --to-revisions <previous-revision>=100 --region <region> --project <staging-project>
```

Rollback must not target production services.

## Staging E2E Checklist

Run these checks only after Grantex staging and AgenticOrg staging both exist:

- Grantex health.
- Grantex JWKS.
- Grantex commerce well-known.
- Grantex MCP initialize.
- Grantex MCP `tools/list`.
- Grantex MCP `tools/call`.
- AgenticOrg health.
- AgenticOrg MCP discovery.
- AgenticOrg A2A discovery.
- Catalog search.
- Catalog get item.
- Inventory check.
- Cart create.
- Consent request.
- Passport exchange.
- Payment intent create.
- Checkout create.
- Mock webhook paid.
- Mock webhook failed.
- Mock webhook expired.
- Duplicate webhook produces no double transition.
- Manual reconciliation.
- Audit timeline.
- Portal payments, audit, passports, settings, and ops views.
- AgenticOrg demo/eval against real staging endpoints.
- Negative case: denied consent.
- Negative case: missing consent.
- Negative case: revoked or expired passport.
- Negative case: amount cap.
- Negative case: disabled merchant.
- Negative case: untrusted agent.
- Negative case: stale inventory.
- Negative case: unsupported EMI, discount, or warranty.
- Negative case: invalid webhook signature.

The AgenticOrg demo/eval should verify that every commerce action uses `grantex_commerce:*`, that payment intent creation goes to Grantex staging, and that checkout follows Grantex's mock-provider state path from the M8 fix.

## Security Controls

- Restrict staging console access with a staging IAM group.
- Prefer a separate staging GCP project.
- Use staging service accounts for API, UI, worker, and beat.
- Use staging-only Secret Manager names and IAM bindings.
- Use dedicated staging database and Redis. No production database or Redis.
- Use sanitized seed data only.
- Keep logs redacted for Authorization headers, passports, idempotency keys, provider IDs, webhook signatures, and secret names where appropriate.
- Enable rate limits on staging API endpoints and agent execution endpoints.
- Keep commerce provider access routed through Grantex only.
- Keep public UI copy clear that staging is sandbox only.
- Keep rollback scoped to staging service names.

## Cost And Ops Notes

Expected services:

- 4 Cloud Run services: API, UI, worker, beat.
- 1 staging database.
- 1 staging Redis.
- Staging Secret Manager entries.
- Staging logs and metrics.

Suggested minimums:

- API and UI min instances: `0` before formal pilot; consider `1` during scheduled demos.
- Worker min instances: `0` or `1` depending on queue smoke coverage.
- Beat min instances: `1` only if scheduled staging jobs are required.
- Cloud Run max instances: low cap such as `2` to `5` per service until load goals are defined.
- Logs: set a shorter staging retention window than production.

Risk/cost tradeoffs:

- Splitting API, UI, worker, and beat matches production shape but costs more than a temporary API/UI-only staging setup.
- A separate staging GCP project costs more setup time but reduces blast radius.
- Public staging is easier for cross-product E2E; access-gated staging is safer but adds friction.
- Min instances `0` saves money but adds cold-start noise to demos and evals.

## Blockers And Questions Before Implementation

- Confirm whether a separate GCP project exists for AgenticOrg staging or must be created.
- Confirm whether `staging.agenticorg.ai` DNS can be configured.
- Confirm whether staging database and Redis budget is approved.
- Confirm whether GitHub environments, protection rules, and environment secrets can be created.
- Confirm whether API/UI/worker/beat separation is desired for staging or whether a cheaper temporary alternative is acceptable.
- Confirm whether staging should be public with auth/rate limits or IP/access-gated.
- Confirm the preferred Grantex auth method: `GRANTEX_COMMERCE_BEARER_TOKEN`, `GRANTEX_AGENT_ASSERTION`, or `GRANTEX_API_KEY`.
- Confirm which LLM provider keys are required by the staging runtime.
- Confirm that commerce continues to avoid direct Stripe, Plural, Pine, and provider credential paths.

## Implementation Sequence For M10+

1. Create or identify the staging GCP project and staging GitHub environment.
2. Configure `staging.agenticorg.ai` DNS.
3. Provision dedicated staging database and Redis.
4. Create staging-only secrets in Secret Manager and GitHub environment secrets.
5. Add manual staging deploy workflows with no-production guardrails.
6. Deploy `agenticorg-api-staging` by manual approval.
7. Deploy `agenticorg-ui-staging` by manual approval.
8. Deploy `agenticorg-worker-staging` and `agenticorg-beat-staging` only if queue and scheduled-job smoke coverage require them.
9. Run AgenticOrg health, MCP discovery, and A2A discovery.
10. Run Commerce Sales Agent demo/eval against `https://api-staging.grantex.dev`.
11. Record evidence in a staging readiness report without committing secret values or local temp files.

## M9 Confirmation

No deploy was performed. No merge was performed. No production config was changed. No production Commerce V1 flag was enabled. No live payment or live Plural path was enabled. No cloud resources were created by this planning document.
