# Secrets Rotation Runbook

We rotate long-lived secrets on a **quarterly cadence** to limit the
blast radius of a credential leak. Short-lived secrets (JWTs, tokens)
are rotated continuously and don't need an operational runbook.

## What rotates on the quarterly cron

| Secret ID              | Reason to rotate                            |
|------------------------|---------------------------------------------|
| AGENTICORG_SECRET_KEY  | Signs local JWTs + CSRF tokens              |
| AGENTICORG_WEBHOOK_SECRET | Verifies inbound webhooks (Stripe/Plural) |
| GRANTEX_API_KEY        | Service account for Grantex                 |
| LLM provider keys      | Rotated by vendor console (annually)        |

Database passwords and long-lived OAuth refresh tokens are rotated on
separate cadences — see the relevant runbooks.

## What triggers rotation

1. **Scheduled:** `.github/workflows/secrets-rotation.yml` runs at
   02:00 UTC on the 1st of Jan/Apr/Jul/Oct.
2. **Manual:** `gh workflow run secrets-rotation.yml` when a team
   member leaves or we suspect compromise.
3. **Incident:** any SEV-1 auth-related incident immediately triggers
   a rotation plus incident review.

## Dual-read window

Our secrets live in **Google Secret Manager**. When we add a new
version we do **not** immediately disable the old one — we leave it
active for 24 hours so:

- In-flight JWTs signed with the old key continue to validate.
- Webhook payloads queued by Stripe/Plural during the swap still pass
  signature verification.

After 24h a follow-up GitHub Actions job (same workflow, next
schedule) disables the old version.

## Manual rotation

```
gh workflow run secrets-rotation.yml \
  -f secrets=AGENTICORG_SECRET_KEY,AGENTICORG_WEBHOOK_SECRET \
  -f dry_run=false
```

The workflow:
1. Creates a new version of each secret via `gcloud secrets versions add`.
2. Rolls the `agenticorg-api` deployment so new pods read the latest
   version on startup.
3. Emits a `secret_rotated` audit event.

## Verification

After the workflow completes:

```
# Confirm the new version is live
kubectl exec -n agenticorg deploy/agenticorg-api -- \
  python -c "import os; print(len(os.environ['AGENTICORG_SECRET_KEY']))"

# Watch for auth errors in the 30-minute dual-read window
kubectl logs -n agenticorg -l app=api --tail=200 | grep -i 'invalid signature\|jwt'
```

## Rollback

If the new version breaks something:

```
# Find the previous version
gcloud secrets versions list AGENTICORG_SECRET_KEY --project=$PROJECT

# Restore by setting the prior version as "latest"
gcloud secrets versions access <PREVIOUS> --secret=AGENTICORG_SECRET_KEY | \
  gcloud secrets versions add AGENTICORG_SECRET_KEY --data-file=-

# Roll the deployment
kubectl rollout restart deploy/agenticorg-api -n agenticorg
```

## Audit trail

Every rotation writes an entry to the immutable `audit_log` table:

```json
{
  "event_type": "secret_rotated",
  "actor_type": "system",
  "actor_id": "github-actions",
  "action": "rotate",
  "resource_type": "secret",
  "resource_id": "AGENTICORG_SECRET_KEY",
  "outcome": "success"
}
```

SOC 2 auditors pull this via `/api/v1/audit?event_type=secret_rotated`.
