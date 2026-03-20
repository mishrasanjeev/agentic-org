# Deployment Guide

## Deployment Options

| Option | Best For | Setup Time |
|--------|----------|------------|
| Docker Compose | Dev, POC, SMB | <1 hour |
| Kubernetes (Helm) | Enterprise production | 2-4 hours |
| Air-gapped | Defence, regulated banking | 1-2 days |

## Docker Compose (Development)

```bash
# Start all services
docker compose up -d

# Services:
# - API:        http://localhost:8000
# - PostgreSQL:  localhost:5432
# - Redis:       localhost:6379
# - MinIO (S3):  http://localhost:9000 (console: :9001)
```

## Kubernetes (Production)

### Prerequisites
- Kubernetes 1.28+
- Helm 3.x
- PostgreSQL 16 (managed: RDS, Cloud SQL, or in-cluster)
- Redis 7 (managed: ElastiCache, Memorystore, or in-cluster)
- S3-compatible storage

### Install

```bash
# Create namespace
kubectl create namespace agentflow-prod

# Create secrets
kubectl create secret generic agentflow-secrets \
  --namespace agentflow-prod \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
  --from-literal=GRANTEX_CLIENT_SECRET=... \
  --from-literal=AGENTFLOW_DB_URL=postgresql+asyncpg://... \
  --from-literal=AGENTFLOW_SECRET_KEY=$(openssl rand -hex 32)

# Install via Helm
helm upgrade --install agentflow-os ./helm \
  --namespace agentflow-prod \
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
  repository: your-registry/agentflow-os
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

| Component | Spec | HA Config | Est. AWS Cost/Month |
|-----------|------|-----------|-------------------|
| Orchestrator (NEXUS) | 3x 4vCPU/8GB, HPA 3-10 | Active-active, stateless | ~$800 |
| Agent Workers | 6x 2vCPU/4GB, HPA 6-20 | Per-domain node pools | ~$600 |
| API Server | 3x 2vCPU/4GB | Load balanced | ~$300 |
| PostgreSQL | db.r6g.2xlarge + 2 replicas | Multi-AZ, auto-failover | ~$1,200 |
| Redis | cache.r6g.large + replica | Sentinel mode | ~$300 |
| S3 | Standard + intelligent tiering | Cross-region optional | ~$50 |
| ALB + WAF | Application Load Balancer | Multi-AZ | ~$200 |
| Observability | CloudWatch + LangSmith + Prometheus | Managed | ~$300 |

## Database Migrations

Run migrations in order:

```bash
psql -h $DB_HOST -U agentflow -d agentflow \
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
- `AGENTFLOW_DB_URL` — PostgreSQL connection string
- `AGENTFLOW_REDIS_URL` — Redis connection string
- `AGENTFLOW_SECRET_KEY` — 32+ char random string for HMAC signing
- `GRANTEX_CLIENT_ID` / `GRANTEX_CLIENT_SECRET` — OAuth2 credentials
- `AGENTFLOW_JWT_PUBLIC_KEY_URL` — JWKS endpoint for token validation

**Never disable in production:**
- `AGENTFLOW_PII_MASKING=true`
- `AGENTFLOW_AUDIT_RETENTION_YEARS=7`

## CI/CD Pipeline

The 9-stage pipeline runs on every push to `main` and on tag pushes:

1. **Lint** — ruff + mypy + eslint + tsc
2. **Unit Tests** — pytest with 80% coverage gate
3. **Integration Tests** — PostgreSQL + Redis service containers
4. **Security Scan** — bandit (SAST) + pip-audit (dependency CVEs)
5. **Build** — Docker images for API and UI
6. **Staging Deploy** — Helm to staging namespace
7. **Smoke Tests** — Playwright + API e2e tests
8. **Approval Gate** — Manual approval via GitHub environment protection
9. **Production Deploy** — Canary rollout (10% → verify → 100%)

## Health Monitoring

```bash
# API health
curl http://localhost:8000/api/v1/health

# Prometheus metrics
curl http://localhost:8000/metrics

# Key alerts (auto-configured):
# - P95 latency > 5s
# - HITL rate > 5%
# - Agent confidence avg < 0.80
# - Tool error rate > 1%
# - STP rate < 90%
# - Daily LLM cost > $100
# - Circuit breaker open
# - Agent budget > 80%
```
