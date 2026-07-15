# Deployment Guide

> **Current limitation (2026-07-15):** The Cloud Run helper covers API/UI release mechanics only. The repository production workflow remains disabled, worker/beat rollout is not covered by the helper, and no command in this guide substitutes for green required CI, migration evidence, release-manifest/image-digest retention, post-deploy checks, rollback proof, or an approved change owner. Lower Helm/GKE and raw-SQL sections are historical reference material.

> **2026-06-13 status:** production runs on Cloud Run. The default production
> Cloud Run services are `agenticorg-api` and `agenticorg-ui` in
> `asia-southeast1`; Artifact Registry images live in `asia-south1` by
> default. Use separate `CLOUD_RUN_REGION` and `GAR_REGION` overrides if that
> split changes.
>
> Ship a release with the manual Cloud Run helper:
>
> ```bash
> # rebuild + roll out origin/main, then poll public health for the new commit
> bash scripts/deploy_cloud_run.sh --yes
>
> # deploy an existing commit image and run Alembic migrations before traffic moves
> bash scripts/deploy_cloud_run.sh --sha <commit-sha> --skip-build --with-migrations --yes
>
> # stage revisions only; production traffic remains pinned
> bash scripts/deploy_cloud_run.sh --sha <commit-sha> --skip-build --traffic preserve --yes
> ```
>
> The helper verifies both services exist before mutation, prints every command,
> supports `--dry-run`, verifies platform image digests and commit metadata on
> staged revisions, probes the API before UI traffic moves, and refuses to
> report success if public health still returns an older commit. Legacy GKE/Helm
> sections below are preserved for reference and non-default deployment shapes.

## Deployment Options

| Option | Best For | Est. Cost/Month | Setup Time |
|--------|----------|----------------|------------|
| Docker Compose | Dev, POC | $0 (local) | <1 hour |
| **Cloud Run** | **Current managed production** | Usage-based | <1 hour after images exist |
| Legacy Kubernetes Lean | Reference demo shape only | ~$50-70 | 1-2 hours |
| Legacy Kubernetes Production | Reference enterprise shape only | ~$800-3,800 | 2-4 hours |
| Air-gapped | Defence, regulated banking | Varies | 1-2 days |

## Docker Compose (Development)

```bash
# Start all services
docker compose up -d

# Services:
# - API:        http://localhost:8000
# - PostgreSQL:  localhost:5432
# - Redis:       localhost:6379
# - MinIO (S3-compat): http://localhost:9000 (console: :9001)
```

## Cloud Run (Current Production)

The manual helper is the current production rollout path:

```bash
# Defaults:
#   GCP_PROJECT_ID=perfect-period-305406
#   CLOUD_RUN_REGION=asia-southeast1
#   GAR_REGION=asia-south1
#   API_SERVICE=agenticorg-api
#   UI_SERVICE=agenticorg-ui
#   MIGRATE_JOB=agenticorg-migrate

bash scripts/deploy_cloud_run.sh --sha <commit-sha> --skip-build --with-migrations --yes
```

Important behavior:

- `--with-migrations` updates and executes the Cloud Run migration job before
  services are staged.
- API and UI services are updated with `--no-traffic` first, then staged
  revisions are checked by revision object.
- API traffic moves first. Public API health must return the target commit
  before the UI revision is staged or routed.
- `--traffic preserve` stages revisions only and reports `NOT DEPLOYED`.
- `--traffic manual` stages revisions only and prints exact traffic commands.
- `--dry-run` prints planned service updates, migration behavior, and traffic
  actions without touching Cloud Run.

The helper accepts manifest-list and platform digests from Artifact Registry by
inspecting image manifests, then verifies that Cloud Run revision containers
match an acceptable digest and that the API/UI commit env vars match the target
SHA.

Focused regression:

```bash
python -m pytest tests/unit/test_deploy_cloud_run_script.py -q
```

## Legacy Kubernetes Lean (~$50-70/month)

Legacy/reference GCP deployment. Single replica of everything, in-cluster Redis
(skip Memorystore), db-f1-micro Cloud SQL. Use Cloud Run for current managed
production unless a separate deployment decision approves GKE.

```bash
# One-command setup
export GCP_PROJECT_ID=perfect-period-305406
export ANTHROPIC_API_KEY=sk-ant-...
chmod +x infra/gcp-setup-lean.sh
./infra/gcp-setup-lean.sh

# Build and push images
REGION=asia-south1
gcloud auth configure-docker ${REGION}-docker.pkg.dev
docker build -t ${REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/agenticorg/api:latest .
docker build -t ${REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/agenticorg/ui:latest -f Dockerfile.ui .
docker push ${REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/agenticorg/api:latest
docker push ${REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/agenticorg/ui:latest

# Deploy with lean config
helm upgrade --install agenticorg ./helm \
  --namespace agenticorg \
  -f helm/values-lean.yaml \
  --set image.repository=${REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/agenticorg/api \
  --set imageUI.repository=${REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/agenticorg/ui

# Access the API (port-forward for now, enable Ingress when ready)
kubectl port-forward svc/agenticorg-api 8000:8000 -n agenticorg
```

### Lean Cost Breakdown

| Component | Spec | Cost/Month |
|-----------|------|-----------|
| Autopilot Kubernetes runtime | ~1 vCPU, ~2GB (pay per pod) | ~$30-50 |
| Cloud SQL | db-f1-micro (shared, 0.6GB RAM, 10GB SSD) | ~$10 |
| Redis | In-cluster pod (128MB) | $0 |
| Cloud Storage | Minimal | ~$1 |
| Artifact Registry | <1GB | ~$1 |
| Cloud NAT | Outbound traffic | ~$5 |
| **Total** | | **~$47-67** |

### Scaling Up (When You Get Customers)

```bash
# Switch to production values
helm upgrade agenticorg ./helm -n agenticorg -f helm/values.yaml \
  --set image.repository=... --set image.tag=v2.1.0

# Or scale individual components
kubectl scale deployment agenticorg-api --replicas=3 -n agenticorg
```

---

## Kubernetes (Production)

### Prerequisites
- Kubernetes 1.28+
- Helm 3.x
- PostgreSQL 16 (managed: Cloud SQL or in-cluster)
- Redis 7 (managed: Memorystore or in-cluster)
- Google Cloud Storage (or S3-compatible storage for dev)

### Install

```bash
# Create GKE cluster (if not already provisioned)
gcloud container clusters create agenticorg-prod \
  --region asia-south1 \
  --num-nodes 3 \
  --machine-type e2-standard-4 \
  --workload-pool=perfect-period-305406.svc.id.goog

# Create namespace
kubectl create namespace agenticorg-prod

# Store secrets in Google Secret Manager and sync to K8s
gcloud secrets create agenticorg-anthropic-key --replication-policy="user-managed" \
  --locations="asia-south1" --data-file=- <<< "sk-ant-..."
gcloud secrets create agenticorg-grantex-secret --replication-policy="user-managed" \
  --locations="asia-south1" --data-file=- <<< "..."

# Create K8s secret (or use External Secrets Operator to sync from Secret Manager)
kubectl create secret generic agenticorg-secrets \
  --namespace agenticorg-prod \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
  --from-literal=GRANTEX_CLIENT_SECRET=... \
  --from-literal=AGENTICORG_DB_URL=postgresql+asyncpg://... \
  --from-literal=AGENTICORG_SECRET_KEY=$(openssl rand -hex 32)

# Push image to Artifact Registry
gcloud artifacts repositories create agenticorg --repository-format=docker \
  --location=asia-south1
docker tag agenticorg:v2.1.0 asia-south1-docker.pkg.dev/perfect-period-305406/agenticorg/agenticorg:v2.1.0
docker push asia-south1-docker.pkg.dev/perfect-period-305406/agenticorg/agenticorg:v2.1.0

# Install via Helm on GKE
helm upgrade --install agenticorg ./helm \
  --namespace agenticorg-prod \
  --set image.repository=asia-south1-docker.pkg.dev/perfect-period-305406/agenticorg/agenticorg \
  --set image.tag=v2.1.0 \
  --set replicaCount=3 \
  --set autoscaling.enabled=true \
  --set autoscaling.minReplicas=3 \
  --set autoscaling.maxReplicas=20 \
  -f helm/values.yaml
```

### Helm Values (key settings)

```yaml
# helm/values.yaml
replicaCount: 3
image:
  repository: asia-south1-docker.pkg.dev/perfect-period-305406/agenticorg/agenticorg
  tag: "v2.1.0"

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 20
  targetCPUUtilization: 70

agentScaling:
  ap_processor:
    minReplicas: 2
    maxReplicas: 20
  recon_agent:
    minReplicas: 1
    maxReplicas: 5
    schedules:
      - cron: "0 22 25-31 * *"  # Month-end spike
        replicas: 4
```

## Production Infrastructure (100K tasks/day)

| Component | Spec | HA Config | Est. GCP Cost/Month |
|-----------|------|-----------|-------------------|
| Orchestrator (NEXUS) | 3x e2-standard-4, HPA 3-10 | Active-active, stateless | ~$800 |
| Agent Workers | 6x e2-standard-2, HPA 6-20 | Per-domain node pools | ~$600 |
| API Server | 3x e2-standard-2 | Load balanced | ~$300 |
| PostgreSQL | Cloud SQL db-custom-8-32768 + 2 read replicas | Regional HA, auto-failover | ~$1,200 |
| Redis | Memorystore Basic M1 + replica | Standard tier HA | ~$300 |
| Cloud Storage | Standard + Nearline lifecycle | Multi-region optional | ~$50 |
| Cloud Load Balancing + Cloud Armor | Global HTTP(S) LB | Multi-region | ~$200 |
| Observability | Cloud Monitoring + LangSmith + Prometheus | Managed | ~$300 |

## Database Migrations

The canonical application migration path is:

```bash
python scripts/alembic_migrate.py
```

The following raw-SQL sequence is retained only for legacy deployment shapes. Do not run it against a current environment unless an approved migration plan explicitly requires it:

```bash
psql -h $DB_HOST -U agenticorg -d agenticorg \
  -f migrations/001_extensions.sql \
  -f migrations/002_core.sql \
  -f migrations/003_operational.sql \
  -f migrations/004_scaling.sql \
  -f migrations/005_rls.sql \
  -f migrations/006_partitions.sql
```

## Environment Variables

See [`.env.example`](../.env.example) for the complete reference.

**Required for production:**
- `ANTHROPIC_API_KEY` — Claude API key
- `AGENTICORG_DB_URL` — PostgreSQL connection string
- `AGENTICORG_REDIS_URL` — Redis connection string
- `AGENTICORG_SECRET_KEY` — 32+ char random string for HMAC signing
- `GRANTEX_CLIENT_ID` / `GRANTEX_CLIENT_SECRET` — OAuth2 credentials
- `AGENTICORG_JWT_PUBLIC_KEY_URL` — JWKS endpoint for token validation

**Never disable in production:**
- `AGENTICORG_PII_MASKING=true`
- `AGENTICORG_AUDIT_RETENTION_YEARS=7`

## CI/CD Pipeline

CI runs lint, type checks, unit/integration tests, security scans, and image
build checks. Production rollout is currently performed through
`scripts/deploy_cloud_run.sh` after CI is green and the target commit/image is
selected.

Current deploy gates:

1. **Lint/type checks** — Python and TypeScript checks.
2. **Unit/integration tests** — pytest and frontend tests.
3. **Security scan** — `pip-audit`, Bandit, npm audit, and Trivy fail closed on
   high-risk findings.
4. **Image build** — API and UI images are built/pushed or verified.
5. **Migration gate** — `--with-migrations` runs Alembic through Cloud Run job
   before service traffic moves.
6. **Revision verification** — staged revisions must match target image digest
   and commit metadata.
7. **Traffic shift** — API moves first, public health must return the target
   commit, then UI moves.

Legacy Helm/GKE staging and canary instructions in this document are reference
material, not the default production path.

## Health Monitoring

```bash
# API health
curl http://localhost:8000/api/v1/health

# Prometheus metrics
curl http://localhost:8000/metrics

# Example alert targets; verify the deployed rules and retained firing tests:
# - P95 latency > 5s
# - HITL rate > 5%
# - Agent confidence avg < 0.80
# - Tool error rate > 1%
# - STP rate < 90%
# - Daily LLM cost > $100
# - Circuit breaker open
# - Agent budget > 80%
```
