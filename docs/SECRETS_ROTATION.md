# Secrets Rotation Runbook

We rotate long-lived secrets on a **quarterly cadence** to limit the
blast radius of a credential leak. Short-lived secrets (JWTs, tokens)
are rotated continuously and don't need an operational runbook.

## What rotates on the quarterly cron

| Secret ID                       | How it rotates                                                     |
|---------------------------------|--------------------------------------------------------------------|
| CDC_WEBHOOK_SECRET_<CONNECTOR>  | `secrets-rotation.yml` (self-generated HMAC; update the sender too) |
| AGENTICORG_VAULT_KEYRING        | By hand, never with the workflow: [vault key rotation](runbooks/vault-key-rotation.md) (stage, promote, rewrap, retire) |
| AGENTICORG_SECRET_KEY           | Never with the workflow. While a service has no `AGENTICORG_VAULT_KEYRING` or `AGENTICORG_VAULT_KEY` it is also the vault key: [move the vault off it](runbooks/vault-key-rotation.md#moving-off-a-single-key) first. Even then a rotation signs everyone out and moves every provider webhook inbox path (same section) |
| GRANTEX_API_KEY                 | Re-issue in the Grantex console, then add the new version manually |
| STRIPE_WEBHOOK_SECRET / Plural  | Re-issue in the provider console; never generated locally          |
| LLM provider keys               | Rotated by vendor console (annually)                               |

The workflow refuses `AGENTICORG_SECRET_KEY` and any externally issued
credential: replacing a provider-issued key with random bytes does not
"rotate" it, it breaks every call that uses it (audit 2026-09-13). It
does not yet refuse the vault's own secrets (FINDINGS A-84): never pass
it `AGENTICORG_VAULT_KEYRING` or `AGENTICORG_VAULT_KEY`, whose value it
would replace with random bytes that open nothing already stored.

Database passwords and long-lived OAuth refresh tokens are rotated on
separate cadences — see the relevant runbooks.

## What triggers rotation

1. **Scheduled:** `.github/workflows/secrets-rotation.yml` runs at
   02:00 UTC on the 1st of Jan/Apr/Jul/Oct.
2. **Manual:** `gh workflow run secrets-rotation.yml` when a team
   member leaves or we suspect compromise.
3. **Incident:** any SEV-1 auth-related incident immediately triggers
   a rotation plus incident review.

## No dual-read window

Our secrets live in **Google Secret Manager** and are mounted as
`:latest`. The rolled revision reads the new version immediately; the
application has no second-version read path. The previous version is
left enabled only so an operator can roll back. Update the sending side
(connector/provider console) to the new value right after the roll,
verify deliveries, then disable the old version manually.

The credential vault is the exception. `AGENTICORG_VAULT_KEYRING` holds
several keys: the first encrypts and every one decrypts, so a vault key is
rotated by adding the new key next to the old one, re-encrypting stored
data, and only then removing the old key, never by replacing the value.
Follow [vault key rotation](runbooks/vault-key-rotation.md).

## Manual rotation

```
gh workflow run secrets-rotation.yml \
  -f secrets=CDC_WEBHOOK_SECRET_ZOHO_BOOKS,CDC_WEBHOOK_SECRET_HUBSPOT \
  -f dry_run=false
```

The workflow:
1. Creates a new version of each secret via `gcloud secrets versions add`.
2. Rolls the `agenticorg-api`, `agenticorg-worker` and `agenticorg-beat`
   Cloud Run services so every runtime reads the latest version.
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
