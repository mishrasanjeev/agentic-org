# Multi-region failover

Terraform scaffold for a **warm standby** deployment of AgenticOrg in
`asia-south2` (Delhi) that can take over if `asia-south1` (Mumbai)
suffers a regional outage.

## Architecture

```
         ┌──────────────────┐     async replication      ┌──────────────────┐
         │ asia-south1      │  ──────────────────────▶   │ asia-south2      │
         │  - GKE (active)  │                            │  - GKE (standby) │
         │  - Cloud SQL     │                            │  - Cloud SQL     │
         │    primary       │                            │    replica       │
         │  - Redis         │                            │  - Redis (cold)  │
         └──────────────────┘                            └──────────────────┘
                    ▲                                              ▲
                    │                         Cloud DNS primary-backup
                    │                             (api.agenticorg.ai)
                    │
            User browsers
```

- Primary is active-read/write.
- Secondary GKE runs at **2 replicas** (cold) so a failover can scale up
  quickly without waiting for image pulls.
- Cloud SQL replica is **zonal** — promoted manually during failover.
- Cloud DNS uses the `primary_backup` routing policy so DNS already
  knows how to fall back; we still kick it manually for a controlled
  failover.

## Estimated RTO/RPO

| Metric | Target |
|--------|--------|
| RTO    | 60 min (SEV-1 failure of primary) |
| RPO    | 5 min  |

## How to apply

```
cd infra/terraform/multi_region
terraform init
terraform plan \
  -var project_id=agenticorg-prod \
  -var primary_lb_ip=... \
  -var secondary_lb_ip=...

# Review the plan carefully — the scaffold is commented out by default.
# Un-comment resources in main.tf, re-run plan, then apply.
terraform apply
```

## Failover runbook

See `docs/BACKUP_AND_DR.md` — the runbook uses the terraform outputs
here to promote the replica and switch DNS.

## Why this is scaffolded (not live)

- Multi-region is ~40% more expensive. We're deferring the standby
  until our Enterprise pipeline justifies it, but we want the
  terraform ready so the decision is an `apply` away.
- Active-active requires writes to converge — we explicitly rejected
  that model (see `docs/adr/0006-active-passive-dr.md`) because our
  LangGraph checkpoints cannot be safely merged.

## Cost estimate

Rough monthly cost of the standby at 2 replicas:

| Resource                          | Monthly cost (USD) |
|-----------------------------------|--------------------|
| GKE control plane                 | $73                |
| 2× e2-standard-4 nodes            | $200               |
| Cloud SQL replica (db-custom-4-16)| $230               |
| Dual-region GCS (100 GB)          | $24                |
| Cloud DNS with healthchecks       | $2                 |
| **Total**                         | **~$529 / month**  |
