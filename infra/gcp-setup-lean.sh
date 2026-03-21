#!/usr/bin/env bash
# =============================================================================
# AgenticOrg — Lean GCP Setup (~$80-120/month)
# =============================================================================
# This provisions the cheapest viable GCP infrastructure for pre-customer stage.
# Scale up individual components as you onboard real customers.
#
# Estimated monthly cost breakdown:
#   GKE Autopilot pods:  ~$30-50 (pay only for running pods, no idle nodes)
#   Cloud SQL (db-f1-micro): ~$10 (shared-core, 0.6GB RAM, 10GB SSD)
#   Redis (in-cluster):  $0 (skip Memorystore, use K8s Redis pod)
#   Cloud Storage:       ~$1 (minimal storage)
#   Artifact Registry:   ~$1 (first 500MB free)
#   Cloud NAT + IP:      ~$5
#   Total:               ~$47-67/month (+ LLM API costs)
#
# Usage:
#   export GCP_PROJECT_ID=your-project-id
#   chmod +x infra/gcp-setup-lean.sh
#   ./infra/gcp-setup-lean.sh
# =============================================================================

set -euo pipefail

# ── Configuration ──
PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID environment variable}"
REGION="${GCP_REGION:-asia-south1}"
ZONE="${REGION}-a"
CLUSTER_NAME="agenticorg-lean"
DB_INSTANCE="agenticorg-db"
DB_PASSWORD="${AGENTICORG_DB_PASSWORD:-$(openssl rand -hex 16)}"
BUCKET_NAME="${PROJECT_ID}-agenticorg-docs"
NAMESPACE="agenticorg"
REPO_NAME="agenticorg"

echo "=== AgenticOrg — Lean GCP Setup ==="
echo "Project:  ${PROJECT_ID}"
echo "Region:   ${REGION}"
echo "Cluster:  ${CLUSTER_NAME}"
echo ""

# ── 1. Enable required APIs ──
echo "[1/8] Enabling GCP APIs..."
gcloud services enable \
  container.googleapis.com \
  sqladmin.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  --project="${PROJECT_ID}" --quiet

# ── 2. Create Artifact Registry repository ──
echo "[2/8] Creating Artifact Registry..."
gcloud artifacts repositories create "${REPO_NAME}" \
  --repository-format=docker \
  --location="${REGION}" \
  --project="${PROJECT_ID}" \
  --quiet 2>/dev/null || echo "  (already exists)"

# ── 3. Create GKE Autopilot cluster (cheapest — pay per pod, no idle nodes) ──
echo "[3/8] Creating GKE Autopilot cluster..."
gcloud container clusters create-auto "${CLUSTER_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --release-channel=regular \
  --quiet 2>/dev/null || echo "  (already exists)"

gcloud container clusters get-credentials "${CLUSTER_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}"

# ── 4. Create Cloud SQL (db-f1-micro — cheapest: ~$10/month) ──
echo "[4/8] Creating Cloud SQL PostgreSQL (db-f1-micro)..."
gcloud sql instances create "${DB_INSTANCE}" \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region="${REGION}" \
  --storage-size=10 \
  --storage-type=SSD \
  --storage-auto-increase \
  --no-assign-ip \
  --network=default \
  --project="${PROJECT_ID}" \
  --quiet 2>/dev/null || echo "  (already exists)"

# Create database and user
gcloud sql databases create agenticorg \
  --instance="${DB_INSTANCE}" \
  --project="${PROJECT_ID}" \
  --quiet 2>/dev/null || echo "  (already exists)"

gcloud sql users set-password postgres \
  --instance="${DB_INSTANCE}" \
  --password="${DB_PASSWORD}" \
  --project="${PROJECT_ID}" \
  --quiet

# Note: pgvector extension needs to be enabled manually via Cloud SQL admin
echo "  NOTE: Enable pgvector extension via Cloud SQL console or:"
echo "  gcloud sql connect ${DB_INSTANCE} --user=postgres"
echo "  Then run: CREATE EXTENSION IF NOT EXISTS vector;"

# ── 5. Create GCS bucket ──
echo "[5/8] Creating Cloud Storage bucket..."
gcloud storage buckets create "gs://${BUCKET_NAME}" \
  --location="${REGION}" \
  --uniform-bucket-level-access \
  --project="${PROJECT_ID}" \
  --quiet 2>/dev/null || echo "  (already exists)"

# ── 6. Store secrets in Google Secret Manager ──
echo "[6/8] Creating secrets in Secret Manager..."
echo -n "${DB_PASSWORD}" | gcloud secrets create agenticorg-db-password \
  --data-file=- \
  --replication-policy="user-managed" \
  --locations="${REGION}" \
  --project="${PROJECT_ID}" \
  --quiet 2>/dev/null || echo "  (already exists)"

# ── 7. Create K8s namespace and secrets ──
echo "[7/8] Setting up Kubernetes namespace and secrets..."
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

# Get Cloud SQL private IP
DB_IP=$(gcloud sql instances describe "${DB_INSTANCE}" \
  --project="${PROJECT_ID}" \
  --format="value(ipAddresses[0].ipAddress)" 2>/dev/null || echo "pending")

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: agenticorg-secrets
  namespace: ${NAMESPACE}
type: Opaque
stringData:
  AGENTICORG_DB_URL: "postgresql+asyncpg://postgres:${DB_PASSWORD}@${DB_IP}:5432/agenticorg"
  AGENTICORG_REDIS_URL: "redis://agenticorg-redis:6379/0"
  AGENTICORG_SECRET_KEY: "$(openssl rand -hex 32)"
  AGENTICORG_STORAGE_BUCKET: "${BUCKET_NAME}"
  AGENTICORG_STORAGE_REGION: "${REGION}"
  GOOGLE_GEMINI_API_KEY: "${GOOGLE_GEMINI_API_KEY:-set-me}"
  ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY:-}"
EOF

# ── 8. Deploy in-cluster Redis (skip Memorystore to save ~$36/month) ──
echo "[8/8] Deploying in-cluster Redis..."
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agenticorg-redis
  namespace: ${NAMESPACE}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: agenticorg-redis
  template:
    metadata:
      labels:
        app: agenticorg-redis
    spec:
      containers:
        - name: redis
          image: redis:7-alpine
          command: ["redis-server", "--appendonly", "yes", "--maxmemory", "128mb", "--maxmemory-policy", "allkeys-lru"]
          ports:
            - containerPort: 6379
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 200m
              memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: agenticorg-redis
  namespace: ${NAMESPACE}
spec:
  selector:
    app: agenticorg-redis
  ports:
    - port: 6379
      targetPort: 6379
EOF

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Cloud SQL IP:    ${DB_IP}"
echo "DB Password:     ${DB_PASSWORD}"
echo "GCS Bucket:      gs://${BUCKET_NAME}"
echo "AR Registry:     ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}"
echo "K8s Namespace:   ${NAMESPACE}"
echo ""
echo "Next steps:"
echo "  1. Get free Gemini API key at https://aistudio.google.com/apikey"
echo "     Then set it: kubectl -n ${NAMESPACE} patch secret agenticorg-secrets -p '{\"stringData\":{\"GOOGLE_GEMINI_API_KEY\":\"YOUR_KEY\"}}'"
echo "  2. Build and push images:"
echo "     gcloud auth configure-docker ${REGION}-docker.pkg.dev"
echo "     docker build -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/api:latest ."
echo "     docker build -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/ui:latest -f Dockerfile.ui ."
echo "     docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/api:latest"
echo "     docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/ui:latest"
echo "  3. Deploy with Helm:"
echo "     helm upgrade --install agenticorg ./helm -n ${NAMESPACE} -f helm/values-lean.yaml \\"
echo "       --set image.repository=${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/api"
echo "  4. Run migrations against Cloud SQL"
echo "  5. Enable pgvector extension on Cloud SQL"
