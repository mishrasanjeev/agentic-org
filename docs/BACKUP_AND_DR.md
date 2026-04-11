# Backup and Disaster Recovery

This document describes how AgenticOrg's managed SaaS deployment on
Google Cloud (GCP) protects customer data against loss and how we
recover from failures.

## Scope

Covered:
- `agenticorg-prod` Cloud SQL (PostgreSQL) database.
- Cloud Storage buckets holding uploaded documents and agent artifacts.
- Redis (Memorystore) session / cache state.
- Application config (Secret Manager).
- The GKE cluster `agenticorg-prod-gke` and all workloads.

Not covered (customer-managed):
- Credentials stored in the customer's own KMS (if they use BYOK).
- Customer-uploaded files that have been hard-deleted via DSAR.

## RTO / RPO

| Subsystem         | RPO    | RTO    |
|-------------------|--------|--------|
| Postgres          | 5 min  | 60 min |
| Cloud Storage     | 0      | 15 min |
| Redis cache       | n/a (rebuildable) | 5 min |
| Secrets           | 0      | instant (replicated in SM) |

RPO = max acceptable data loss. RTO = max acceptable downtime.

## Backup schedule

### PostgreSQL (Cloud SQL)
- **Automated daily backups**: 02:00 IST, retained 30 days (Pro) or
  365 days (Enterprise).
- **Point-in-time recovery (PITR)** via continuous WAL archive to GCS.
  We keep 7 days of WAL to cover the RPO.
- **Cross-region replica** in `asia-south2` with async replication.
  Promoted manually for regional failover.

### Cloud Storage
- Versioning enabled on all production buckets.
- `objectViewer` IAM binding restricted to production service account.
- Dual-region buckets for any artifact larger than 1 MB.

### Secrets
- Secret Manager replicates automatically across all `asia-south1`
  zones. No additional backup needed.

## Restore procedure (runbook)

### Scenario: accidental table truncation or bad migration
1. Page on-call SRE → declare SEV-1.
2. From the Cloud SQL console, clone the instance to a new instance at
   timestamp T − 5 minutes.
3. Run `scripts/compare_snapshots.sql` to identify affected rows.
4. Dump the affected tables from the clone:
   `gcloud sql export sql <clone> gs://agenticorg-dr/tmp/<ts>.sql.gz --table=<tables>`
5. Restore into prod:
   `gcloud sql import sql agenticorg-prod gs://agenticorg-dr/tmp/<ts>.sql.gz`
6. Verify via `scripts/verify_restore.py`.
7. Write incident report within 24h.

### Scenario: full database loss
1. Declare SEV-1.
2. Promote the cross-region replica:
   `gcloud sql instances promote-replica agenticorg-prod-replica`
3. Update the `AGENTICORG_DB_URL` secret to point at the promoted
   instance.
4. Rolling-restart API deployments so they pick up the new URL:
   `kubectl rollout restart deployment/api -n agenticorg`
5. Restart workers and the websocket feed pods.
6. Verify health via `/api/v1/health` and the status page.

### Scenario: GKE cluster loss
1. Re-run the Terraform in `infra/terraform/gke/`. This takes ~20 min.
2. Redeploy via ArgoCD sync.
3. Secrets reattach from Secret Manager automatically.
4. Reconnect the database (unchanged).

## DR testing

We run a DR drill on the **first Saturday of every quarter**:
1. Spin up a DR staging cluster from scratch.
2. Restore the latest prod backup into a fresh Cloud SQL instance.
3. Point the DR staging cluster at the restored DB.
4. Run the E2E smoke suite against DR staging.
5. Measure RTO and compare against the target.
6. File any gaps as follow-up tickets.

Drill results are logged in `docs/dr-drills/` and shared with
Enterprise customers on request.

## Responsibilities

- **SRE on-call**: executes runbooks, declares severity, posts updates
  to the status page.
- **CTO**: approves cross-region failover (DNS change is not reversible
  without another failover).
- **Customer success**: notifies affected customers.

## Contacts

- On-call: paged via Grafana → PagerDuty rotation `agenticorg-sre`.
- Escalation: CTO (+91-xxxxxxxxxx) for any SEV-1 > 2 hours.
