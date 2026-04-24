# Enterprise Release — Ops Runbook

**Status:** open — required to close Codex 2026-04-24 enterprise GA sign-off.
**Owner:** platform ops / SRE
**Last updated:** 2026-04-24

Codex's 2026-04-24 verdict refused GA sign-off with three residuals. One is
code-fixable (commit SHA injection — handled by PR #300). The other two are
**operations-configuration tasks** — secrets + service provisioning that
must be done by ops with access to the production GCP project. This runbook
enumerates the exact steps.

All three items are independent; you can close them in any order. Each one
ends with a verification command that proves the fix is live.

## 1. Billing — wire Stripe and Pine Labs credentials (P0)

**Symptom:** `curl https://app.agenticorg.ai/api/v1/billing/health` returns
`ready_for_release: false` with both `stripe_configured` and
`pinelabs_configured` false.

**Why it blocks release:** `/billing/subscribe` and
`/billing/subscribe/india` both return **503** until the relevant API
keys are present. Customers cannot complete a paid signup.

### Steps

1. **Stripe (USD, global)** — in the Stripe Dashboard's Live Mode:
   - Create a restricted API key with `subscriptions:write`, `customers:write`,
     `checkout_sessions:write`, and `webhooks:read`. Copy the `sk_live_...` value.
   - Create a webhook endpoint at `https://app.agenticorg.ai/api/v1/billing/webhook/stripe`
     subscribing to `customer.subscription.*`, `invoice.*`,
     `checkout.session.completed`. Copy the signing secret (`whsec_...`).
   - Create two Price objects for the Pro + Enterprise plans. Copy the
     `price_...` IDs.

   Store in GCP Secret Manager under the project
   `perfect-period-305406` (production):

   ```bash
   printf "sk_live_..." | gcloud secrets create STRIPE_SECRET_KEY --data-file=- --project=perfect-period-305406
   printf "whsec_..."    | gcloud secrets create STRIPE_WEBHOOK_SECRET --data-file=- --project=perfect-period-305406
   printf "price_pro_id" | gcloud secrets create STRIPE_PRICE_PRO --data-file=- --project=perfect-period-305406
   printf "price_ent_id" | gcloud secrets create STRIPE_PRICE_ENTERPRISE --data-file=- --project=perfect-period-305406
   ```

2. **Pine Labs Plural (INR, India)** — in the Plural merchant portal:
   - Generate a production OAuth client (Client ID + Client Secret).
   - Configure the webhook to `https://app.agenticorg.ai/api/v1/billing/webhook/plural`
     with HMAC-SHA256 signing. Copy the base64 webhook secret.

   ```bash
   printf "..." | gcloud secrets create PLURAL_CLIENT_ID --data-file=- --project=perfect-period-305406
   printf "..." | gcloud secrets create PLURAL_CLIENT_SECRET --data-file=- --project=perfect-period-305406
   printf "..." | gcloud secrets create PLURAL_WEBHOOK_SECRET --data-file=- --project=perfect-period-305406
   printf "production" | gcloud secrets create PLURAL_ENV --data-file=- --project=perfect-period-305406
   ```

3. **Plumb into Helm `secretEnv`** — extend `helm/values-production.yaml`:

   ```yaml
   secretEnv:
     GRANTEX_API_KEY: {secretName: grantex-api-key, version: latest}
     STRIPE_SECRET_KEY: {secretName: STRIPE_SECRET_KEY, version: latest}
     STRIPE_WEBHOOK_SECRET: {secretName: STRIPE_WEBHOOK_SECRET, version: latest}
     STRIPE_PRICE_PRO: {secretName: STRIPE_PRICE_PRO, version: latest}
     STRIPE_PRICE_ENTERPRISE: {secretName: STRIPE_PRICE_ENTERPRISE, version: latest}
     PLURAL_CLIENT_ID: {secretName: PLURAL_CLIENT_ID, version: latest}
     PLURAL_CLIENT_SECRET: {secretName: PLURAL_CLIENT_SECRET, version: latest}
     PLURAL_WEBHOOK_SECRET: {secretName: PLURAL_WEBHOOK_SECRET, version: latest}
     PLURAL_ENV: {secretName: PLURAL_ENV, version: latest}
   ```

   Then `helm upgrade agenticorg ./helm -f helm/values-production.yaml` to roll the pods.

4. **Mandatory sandbox test transaction** before flipping the Upgrade button in
   the UI. Use `PLURAL_ENV=sandbox` first, run a throwaway checkout, verify
   `/api/v1/billing/webhook/plural` receives the event and the tenant's plan
   flips to paid. Then swap to `production` in Secret Manager.

### Verification

```bash
curl -s https://app.agenticorg.ai/api/v1/billing/health | jq '{stripe_configured, pinelabs_configured, ready_for_release}'
# Expect: {"stripe_configured": true, "pinelabs_configured": true, "ready_for_release": true}
```

## 2. Knowledge Base — deploy RAGFlow sidecar (P1)

**Symptom:** `/api/v1/knowledge/health` returns `ragflow_reachable: false`
and `effective_mode: "pgvector"`. The system is alive on fallback but
running in degraded mode.

**Why it blocks release:** The platform advertises semantic retrieval with
reranking; pgvector-only fallback is roughly 35% lower retrieval relevance
on long docs. Acceptable as degraded graceful fallback; not acceptable as
steady-state for an enterprise launch.

### Steps

1. Deploy RAGFlow as a separate Kubernetes Deployment + Service in the
   `agenticorg` namespace. Reference the upstream Helm chart at
   `https://github.com/infiniflow/ragflow`. Minimum 2 CPU / 4 GiB per replica
   + a PVC for the vector index.

2. Create a RAGFlow API key in the RAGFlow admin UI. Store in Secret Manager:

   ```bash
   printf "..." | gcloud secrets create RAGFLOW_API_KEY --data-file=- --project=perfect-period-305406
   ```

3. Update `helm/values-production.yaml`:

   ```yaml
   env:
     # ... existing keys ...
     RAGFLOW_API_URL: "http://ragflow.agenticorg.svc.cluster.local:9380"

   secretEnv:
     RAGFLOW_API_KEY: {secretName: RAGFLOW_API_KEY, version: latest}
   ```

4. `helm upgrade` to roll the API pods with the new env.

### Verification

```bash
curl -s https://app.agenticorg.ai/api/v1/knowledge/health | jq '{ragflow_configured, ragflow_reachable, effective_mode}'
# Expect: {"ragflow_configured": true, "ragflow_reachable": true, "effective_mode": "ragflow"}
```

## 3. Commit SHA provenance — code fix shipped in PR #300

**Symptom:** `/api/v1/health` returned `commit: "unknown"` even after the
code was shipped.

**Root cause:** deploy-production in `.github/workflows/deploy.yml` used
`kubectl set image` (which only updates the image tag; env stays stale).
The `AGENTICORG_GIT_SHA` env var was never injected.

**Fix (PR #300):** added a `kubectl set env deploy/agenticorg-api
AGENTICORG_GIT_SHA=${{ github.sha }}` step after the image update.

### Verification

After the next deploy, `/api/v1/health` must return the actual commit SHA:

```bash
curl -s https://app.agenticorg.ai/api/v1/health | jq .commit
# Expect: a 40-char lowercase hex string, not "unknown".
```

## Sign-off checklist

- [ ] §1 Stripe + Plural secrets in GCP Secret Manager.
- [ ] §1 `helm upgrade` rolled. `/billing/health` shows `ready_for_release: true`.
- [ ] §1 Sandbox test transaction succeeded end-to-end.
- [ ] §2 RAGFlow service + secret deployed. `/knowledge/health` shows
      `effective_mode: "ragflow"`.
- [ ] §3 Next deploy lands PR #300; `/health` returns a real commit SHA.

Once all five boxes are checked, re-request Codex review for enterprise GA
sign-off.
