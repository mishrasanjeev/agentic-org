# Backup and Disaster Recovery

This is a recovery planning document, not evidence that every control below is
active in production. Before relying on a backup, replica, or failover route,
the operator must verify the current cloud configuration and a recent restore
test. Do not quote an RTO or RPO as achieved without measured drill evidence.

## Current evidence boundary

- The current deployment uses Cloud Run services `agenticorg-api` and `agenticorg-ui`,
  plus a migration job. See `scripts/deploy_cloud_run.sh` for the deployment path.
- `infra/terraform/multi_region/README.md` explicitly describes a **scaffold**
  whose standby resources are commented out. It is not an active failover
  environment, and its GKE diagram is not the current Cloud Run topology.
- This repository has no `docs/dr-drills/` evidence directory, no
  `scripts/compare_snapshots.sql`, and no `scripts/verify_restore.py`. Older
  versions of this page referred to them as if they existed; do not use those
  steps as an executable runbook.
- Cloud SQL backup/PITR retention, replica health, object versioning, secret
  replication, pager coverage, and a restore drill are **not verified by this
  repository**. Confirm each in the deployed environment before claiming it.

## Recovery targets, not guarantees

| Dependency | Proposed recovery target | Evidence required before commitment |
| --- | --- | --- |
| PostgreSQL | RPO <= 5 min, RTO <= 60 min | Backup/PITR settings, restore timestamp, measured full restore and application validation |
| Object storage | Versioned recovery, RTO <= 60 min | Bucket policy, deletion/version test, application readback |
| Redis | Rebuildable cache/session state | Fail-closed auth behavior, queue/session recovery, restart test |
| API/UI | Rollback to known-good commit | Revision traffic rollback drill, health and authenticated smoke |

The proposed targets require product and infrastructure owner approval. A
regional outage may exceed them until a deployed standby, DNS procedure, and
restore drill are proven. Redis may be rebuildable as a cache, but session and
durable-work behavior must be tested separately; do not assume zero impact.

## Before any production recovery action

1. Declare an incident and freeze non-essential deploys and migrations.
2. Record the affected region, deployed commit, migration version, last known
   good revision, database backup/PITR coverage, and object-store status.
3. Have the authorized operator verify backups and restore into an isolated
   environment. Never overwrite production as the first validation step.
4. Compare tenant-scoped row counts and critical invariants, then run health,
   authentication, workflow resume, knowledge readback, and audit checks.
5. Approve traffic switch separately; keep rollback and data-reconciliation
   owners on the incident bridge.
6. Record actual data-loss interval and time-to-recovery in a dated drill or
   incident report. Only then update external RPO/RTO claims.

## Failure scenarios

### Bad application revision

Use the repository's deployment procedure to identify a known-good Cloud Run
revision. An authorized operator may roll traffic back after checking schema
compatibility. Recheck API/UI health, DB/Redis, auth, and critical workflows.
Do not roll back a database migration automatically with an application image.

### Database loss or corrupt migration

Confirm the available Cloud SQL backup/PITR window in the live project.
Restore to a new instance, verify schema and tenant data, and only then plan a
controlled connection switch. A replica is not a substitute for a backup:
logical corruption may replicate. The multi-region Terraform scaffold must
not be treated as a ready-to-promote replica.

### Region or Redis outage

Confirm whether an independent target region and data replica actually exist.
If not, escalate as an outage with a recovery plan rather than following the
old scaffold as if it were live. For Redis, verify auth/session behavior and
whether queued jobs require replay or reconciliation before restoring traffic.

## Evidence to create

- Inventory actual Cloud SQL backup/PITR retention, replica status, Cloud Run
  revisions, object versioning, and secret replication without exposing secrets.
- Run a quarterly isolated restore drill and save a dated report with backup
  timestamp, restore duration, checksums/row counts, tenant isolation,
  authenticated smoke, actual RPO/RTO, and sign-off.
- Replace the GKE-specific scaffold with an approved Cloud Run-compatible DR
  plan before provisioning standby resources.
- Alert on backup failures, replication lag (if deployed), restore-test age,
  DB pool saturation, Redis failures, and queue backlog.
