#!/usr/bin/env bash
# Manual Cloud Run deploy helper.
#
# Captures the manual `gcloud run services update` routine that's
# been the only path to production since the GKE cluster was
# removed on 2026-04-25 (see .github/workflows/deploy.yml line
# ~360 for the disabled GKE block). Until the Cloud Run CI deploy
# rewrite lands, this script is the source of truth for "how do
# I roll out main right now".
#
# What it does (in order):
#   1. Sanity-check that the working tree is clean and on the
#      requested commit (default: origin/main).
#   2. Build + push the API image (Dockerfile) and UI image
#      (Dockerfile.ui) tagged with the deploy SHA + :latest.
#   3. Optionally run alembic migrations as a Cloud Run job
#      (--with-migrations, requires an `agenticorg-migrate` job
#      to already exist; create it with --create-migrate-job).
#   4. `gcloud run services update agenticorg-api` with the new
#      image + AGENTICORG_GIT_SHA env var so /health surfaces the
#      deployed commit (Codex 2026-04-24 enterprise sign-off
#      blocker, originally fixed for the GKE path).
#   5. `gcloud run services update agenticorg-ui` with the UI
#      image.
#   6. Poll https://app.agenticorg.ai/api/v1/health until it
#      reports the new commit. Fails loudly if the deploy didn't
#      take.
#
# Conservative on purpose: every step prints what it's about to
# do, and `--dry-run` only prints. Refuses to touch anything
# unless the caller passes the explicit commit.

set -euo pipefail

# ── Defaults — override via env or flags ─────────────────────────────
GCP_PROJECT_ID="${GCP_PROJECT_ID:-perfect-period-305406}"
GCP_REGION="${GCP_REGION:-asia-south1}"
GAR_REGISTRY="${GAR_REGISTRY:-${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/agenticorg}"
API_SERVICE="${API_SERVICE:-agenticorg-api}"
UI_SERVICE="${UI_SERVICE:-agenticorg-ui}"
MIGRATE_JOB="${MIGRATE_JOB:-agenticorg-migrate}"
HEALTH_URL="${HEALTH_URL:-https://app.agenticorg.ai/api/v1/health}"
PROD_BRANCH="${PROD_BRANCH:-origin/main}"

DEPLOY_SHA=""
DRY_RUN=0
RUN_MIGRATIONS=0
CREATE_MIGRATE_JOB=0
SKIP_BUILD=0

usage() {
  cat <<'EOF'
Usage: scripts/deploy_cloud_run.sh [options]

Options:
  --sha <sha>            Deploy this commit (default: origin/main HEAD).
  --with-migrations      Run alembic migrations as a Cloud Run job before
                         updating services. Requires the migrate job to
                         exist (create with --create-migrate-job).
  --create-migrate-job   Create/refresh the agenticorg-migrate Cloud Run
                         job from the API image (does not execute it).
  --skip-build           Don't rebuild/push images. Useful when the SHA
                         was already built on a prior run.
  --dry-run              Print the commands without running them.
  -h, --help             Show this help.

Environment overrides:
  GCP_PROJECT_ID  GCP_REGION  GAR_REGISTRY
  API_SERVICE     UI_SERVICE  MIGRATE_JOB
  HEALTH_URL      PROD_BRANCH

Examples:
  scripts/deploy_cloud_run.sh                       # deploy origin/main
  scripts/deploy_cloud_run.sh --with-migrations     # deploy + run migrations
  scripts/deploy_cloud_run.sh --sha abcd123 --dry-run
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) DEPLOY_SHA="$2"; shift 2 ;;
    --with-migrations) RUN_MIGRATIONS=1; shift ;;
    --create-migrate-job) CREATE_MIGRATE_JOB=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 2 ;;
  esac
done

run() {
  echo "+ $*"
  if [[ $DRY_RUN -eq 0 ]]; then
    "$@"
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1" >&2; exit 1; }
}

require_cmd gcloud
require_cmd docker
require_cmd git
require_cmd curl

# ── 1. Resolve commit ────────────────────────────────────────────────
if [[ -z "$DEPLOY_SHA" ]]; then
  git fetch --quiet origin "${PROD_BRANCH#origin/}"
  DEPLOY_SHA=$(git rev-parse "$PROD_BRANCH")
fi
SHORT_SHA="${DEPLOY_SHA:0:7}"

echo "── Deploy plan ──────────────────────────────────────────────"
echo "  project   : $GCP_PROJECT_ID"
echo "  region    : $GCP_REGION"
echo "  registry  : $GAR_REGISTRY"
echo "  api svc   : $API_SERVICE"
echo "  ui svc    : $UI_SERVICE"
echo "  commit    : $DEPLOY_SHA ($SHORT_SHA)"
echo "  migrations: $([[ $RUN_MIGRATIONS -eq 1 ]] && echo yes || echo no)"
echo "  build     : $([[ $SKIP_BUILD -eq 1 ]] && echo skip || echo yes)"
echo "  dry-run   : $([[ $DRY_RUN -eq 1 ]] && echo yes || echo no)"
echo "─────────────────────────────────────────────────────────────"

if [[ $DRY_RUN -eq 0 ]]; then
  read -rp "Proceed? [y/N] " confirm
  case "$confirm" in
    y|Y|yes|YES) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
fi

# ── 2. Sanity-check service existence before writing anything ────────
for svc in "$API_SERVICE" "$UI_SERVICE"; do
  if ! gcloud run services describe "$svc" \
        --project="$GCP_PROJECT_ID" --region="$GCP_REGION" \
        --format="value(status.url)" >/dev/null 2>&1; then
    echo "::error::Cloud Run service '$svc' not found in $GCP_REGION/$GCP_PROJECT_ID." >&2
    echo "  Override with API_SERVICE/UI_SERVICE/GCP_REGION env vars." >&2
    exit 1
  fi
done

API_IMAGE="${GAR_REGISTRY}/agenticorg:${DEPLOY_SHA}"
UI_IMAGE="${GAR_REGISTRY}/agenticorg-ui:${DEPLOY_SHA}"

# ── 3. Build + push images ───────────────────────────────────────────
if [[ $SKIP_BUILD -eq 0 ]]; then
  run gcloud auth configure-docker "${GCP_REGION}-docker.pkg.dev" --quiet

  run docker build \
    -t "$API_IMAGE" \
    -t "${GAR_REGISTRY}/agenticorg:latest" \
    .
  run docker push "$API_IMAGE"
  run docker push "${GAR_REGISTRY}/agenticorg:latest"

  run docker build \
    -t "$UI_IMAGE" \
    -t "${GAR_REGISTRY}/agenticorg-ui:latest" \
    -f Dockerfile.ui .
  run docker push "$UI_IMAGE"
  run docker push "${GAR_REGISTRY}/agenticorg-ui:latest"
fi

# ── 4. Create migrate job (optional, idempotent-ish) ────────────────
if [[ $CREATE_MIGRATE_JOB -eq 1 ]]; then
  if gcloud run jobs describe "$MIGRATE_JOB" \
       --project="$GCP_PROJECT_ID" --region="$GCP_REGION" >/dev/null 2>&1; then
    run gcloud run jobs update "$MIGRATE_JOB" \
      --project="$GCP_PROJECT_ID" \
      --region="$GCP_REGION" \
      --image="$API_IMAGE" \
      --command="python" \
      --args="scripts/alembic_migrate.py" \
      --set-env-vars="AGENTICORG_DDL_MANAGED_BY_ALEMBIC=true"
  else
    run gcloud run jobs create "$MIGRATE_JOB" \
      --project="$GCP_PROJECT_ID" \
      --region="$GCP_REGION" \
      --image="$API_IMAGE" \
      --command="python" \
      --args="scripts/alembic_migrate.py" \
      --set-env-vars="AGENTICORG_DDL_MANAGED_BY_ALEMBIC=true"
  fi
fi

# ── 5. Run migrations (optional) ─────────────────────────────────────
if [[ $RUN_MIGRATIONS -eq 1 ]]; then
  if ! gcloud run jobs describe "$MIGRATE_JOB" \
        --project="$GCP_PROJECT_ID" --region="$GCP_REGION" >/dev/null 2>&1; then
    echo "::error::Migrate job '$MIGRATE_JOB' missing — re-run with --create-migrate-job first." >&2
    exit 1
  fi
  # --update flag rewires the job to the deploy SHA so we run the
  # migrations from the same image we're about to roll out.
  run gcloud run jobs update "$MIGRATE_JOB" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --image="$API_IMAGE"
  run gcloud run jobs execute "$MIGRATE_JOB" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --wait
fi

# ── 6. Roll out new images ───────────────────────────────────────────
run gcloud run services update "$API_SERVICE" \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --image="$API_IMAGE" \
  --update-env-vars="AGENTICORG_GIT_SHA=${DEPLOY_SHA}"

run gcloud run services update "$UI_SERVICE" \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --image="$UI_IMAGE"

# ── 7. Health check — confirm the new commit is live ────────────────
if [[ $DRY_RUN -eq 1 ]]; then
  echo "[dry-run] would poll $HEALTH_URL until commit=$SHORT_SHA"
  exit 0
fi

echo "Polling $HEALTH_URL for commit=$SHORT_SHA …"
for attempt in $(seq 1 30); do
  resp=$(curl -fsS "$HEALTH_URL" 2>/dev/null || true)
  status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
  commit=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('commit',''))" 2>/dev/null || true)
  if [[ "$status" == "healthy" && "${commit:0:7}" == "$SHORT_SHA" ]]; then
    echo "✓ Deploy verified: status=healthy commit=$commit"
    exit 0
  fi
  echo "  attempt $attempt: status=${status:-?} commit=${commit:-?} (want $SHORT_SHA) — retrying in 10s"
  sleep 10
done

echo "::error::Health check did not converge to $SHORT_SHA within 5 minutes." >&2
echo "  Last response: $resp" >&2
exit 1
