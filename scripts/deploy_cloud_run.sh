#!/usr/bin/env bash
# Manual Cloud Run deploy helper.
#
# Captures the manual Cloud Run rollout routine that's been the
# production path since the GKE cluster was removed on 2026-04-25.
#
# What it does (in order):
#   1. Resolves the explicit deploy commit.
#   2. Builds/pushes or verifies the API and UI images for that commit.
#   3. Optionally runs Alembic migrations through the existing Cloud Run job.
#   4. Captures existing Cloud Run traffic before touching services.
#   5. Updates service templates with --no-traffic and records the new revision
#      names created for the target images.
#   6. Depending on --traffic:
#      - latest: probe the API revision by tag when possible, then route 100%
#        traffic to the captured API revision, verify public health, and only
#        then stage and route the UI revision.
#      - preserve: stage revisions only and report NOT DEPLOYED.
#      - manual: stage revisions only and print exact traffic commands.
#
# The script must never silently report success while production traffic remains
# pinned to an older revision.

set -euo pipefail

# Defaults. Override via env or flags.
GCP_PROJECT_ID="${GCP_PROJECT_ID:-perfect-period-305406}"
CLOUD_RUN_REGION="${CLOUD_RUN_REGION:-${GCP_REGION:-asia-southeast1}}"
GAR_REGION="${GAR_REGION:-asia-south1}"
GCP_REGION="$CLOUD_RUN_REGION"
GAR_HOST="${GAR_HOST:-${GAR_REGION}-docker.pkg.dev}"
GAR_REGISTRY="${GAR_REGISTRY:-${GAR_HOST}/${GCP_PROJECT_ID}/agenticorg}"
API_SERVICE="${API_SERVICE:-agenticorg-api}"
UI_SERVICE="${UI_SERVICE:-agenticorg-ui}"
MIGRATE_JOB="${MIGRATE_JOB:-agenticorg-migrate}"
HEALTH_URL="${HEALTH_URL:-https://app.agenticorg.ai/api/v1/health}"
API_HEALTH_PATH="${API_HEALTH_PATH:-/api/v1/health}"
PROD_BRANCH="${PROD_BRANCH:-origin/main}"
TRAFFIC_MODE="${TRAFFIC_MODE:-latest}"

DEPLOY_SHA=""
DRY_RUN=0
ASSUME_YES="${ASSUME_YES:-0}"
RUN_MIGRATIONS=0
CREATE_MIGRATE_JOB=0
SKIP_BUILD=0

usage() {
  cat <<'EOF'
Usage: scripts/deploy_cloud_run.sh [options]

Options:
  --sha <sha>             Deploy this commit (default: origin/main HEAD).
  --with-migrations       Run Alembic migrations as a Cloud Run job before
                          updating services. Requires the migrate job to
                          exist (create with --create-migrate-job).
  --create-migrate-job    Create/refresh the agenticorg-migrate Cloud Run
                          job from the API image (does not execute it).
  --skip-build            Don't rebuild/push images. The commit images must
                          already exist in Artifact Registry.
  --traffic <mode>        Traffic behavior after staging revisions:
                            latest   route 100% to the new API/UI revisions
                                     after readiness/probe checks (default)
                            preserve stage revisions with --no-traffic and
                                     report NOT DEPLOYED
                            manual   stage revisions with --no-traffic and
                                     print update-traffic commands
  -y, --yes               Do not prompt for confirmation.
  --dry-run               Print the commands and planned traffic changes
                          without running them.
  -h, --help              Show this help.

Environment overrides:
  GCP_PROJECT_ID  CLOUD_RUN_REGION  GCP_REGION
  GAR_REGION      GAR_HOST          GAR_REGISTRY
  API_SERVICE     UI_SERVICE  MIGRATE_JOB
  HEALTH_URL      API_HEALTH_PATH  PROD_BRANCH

Examples:
  scripts/deploy_cloud_run.sh --sha abcd123 --skip-build
  scripts/deploy_cloud_run.sh --sha abcd123 --skip-build --traffic preserve
  scripts/deploy_cloud_run.sh --sha abcd123 --skip-build --dry-run
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) DEPLOY_SHA="$2"; shift 2 ;;
    --with-migrations) RUN_MIGRATIONS=1; shift ;;
    --create-migrate-job) CREATE_MIGRATE_JOB=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --traffic) TRAFFIC_MODE="$2"; shift 2 ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 2 ;;
  esac
done

case "$TRAFFIC_MODE" in
  latest|preserve|manual) ;;
  *) echo "::error::Unknown --traffic mode '$TRAFFIC_MODE'. Use latest, preserve, or manual." >&2; exit 2 ;;
esac

run() {
  echo "+ $*"
  if [[ $DRY_RUN -eq 0 ]]; then
    "$@"
  fi
}

run_may_fail() {
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
require_cmd mktemp

resolve_python_bin() {
  local candidate

  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import json
import sys
PY
    then
      echo "$PYTHON_BIN"
      return 0
    fi
    echo "::error::PYTHON_BIN is set but is not usable: $PYTHON_BIN" >&2
    exit 1
  fi

  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" - <<'PY' >/dev/null 2>&1
import json
import sys
PY
    then
      echo "$candidate"
      return 0
    fi
  done

  echo "Missing: usable python3 or python" >&2
  exit 1
}

PYTHON_BIN="$(resolve_python_bin)"

service_json() {
  gcloud run services describe "$1" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --format=json
}

service_value() {
  gcloud run services describe "$1" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --format="value($2)"
}

traffic_summary() {
  local json_file
  local rc=0

  json_file="$(mktemp)"
  service_json "$1" > "$json_file"
  "$PYTHON_BIN" - "$json_file" <<'PY' || rc=$?
import json, sys

with open(sys.argv[1], encoding="utf-8") as fh:
    svc = json.load(fh)
traffic = svc.get("status", {}).get("traffic", []) or []
items = []
for item in traffic:
    revision = item.get("revisionName") or ("LATEST" if item.get("latestRevision") else "unknown")
    percent = item.get("percent", 0)
    tag = item.get("tag")
    url = item.get("url")
    suffix = []
    if tag:
        suffix.append(f"tag={tag}")
    if url:
        suffix.append(f"url={url}")
    extra = " (" + ", ".join(suffix) + ")" if suffix else ""
    items.append(f"{revision}={percent}%{extra}")
print(", ".join(items) if items else "none")
PY
  rm -f "$json_file"
  return "$rc"
}

traffic_to_revisions() {
  local json_file
  local rc=0

  json_file="$(mktemp)"
  service_json "$1" > "$json_file"
  "$PYTHON_BIN" - "$json_file" <<'PY' || rc=$?
import json, sys

with open(sys.argv[1], encoding="utf-8") as fh:
    svc = json.load(fh)
traffic = svc.get("status", {}).get("traffic", []) or []
parts = []
for item in traffic:
    percent = item.get("percent", 0)
    if not percent:
        continue
    revision = item.get("revisionName")
    if not revision and item.get("latestRevision"):
        revision = "LATEST"
    if revision:
        parts.append(f"{revision}={percent}")
print(",".join(parts))
PY
  rm -f "$json_file"
  return "$rc"
}

tagged_revision_url() {
  local svc="$1"
  local tag="$2"
  local revision="$3"
  local json_file
  local rc=0

  json_file="$(mktemp)"
  service_json "$svc" > "$json_file"
  "$PYTHON_BIN" - "$json_file" "$tag" "$revision" <<'PY' || rc=$?
import json, sys

wanted_tag = sys.argv[2]
wanted_revision = sys.argv[3]
with open(sys.argv[1], encoding="utf-8") as fh:
    svc = json.load(fh)
for item in svc.get("status", {}).get("traffic", []) or []:
    if item.get("tag") == wanted_tag and item.get("revisionName") == wanted_revision:
        print(item.get("url", ""))
        break
PY
  rm -f "$json_file"
  return "$rc"
}

image_digest() {
  gcloud artifacts docker images describe "$1" \
    --project="$GCP_PROJECT_ID" \
    --format="value(image_summary.digest)" 2>/dev/null || true
}

image_acceptable_digests() {
  local image="$1"
  local json_file
  local rc=0

  json_file="$(mktemp)"
  if docker buildx imagetools inspect "$image" --format '{{json .}}' > "$json_file" 2>/dev/null; then
    "$PYTHON_BIN" - "$json_file" <<'PY' || rc=$?
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    payload = json.load(fh)

digests = []

def add(value):
    if isinstance(value, str) and value.startswith("sha256:") and value not in digests:
        digests.append(value)

manifest = payload.get("manifest", {}) or {}
add(manifest.get("digest"))
for item in manifest.get("manifests", []) or []:
    platform = item.get("platform", {}) or {}
    if platform.get("os") == "unknown" and platform.get("architecture") == "unknown":
        continue
    add(item.get("digest"))

print(",".join(digests))
PY
    rm -f "$json_file"
    return "$rc"
  fi

  rm -f "$json_file"
  image_digest "$image"
}

require_image_digest() {
  local image="$1"
  local label="$2"
  local digest
  digest="$(image_acceptable_digests "$image")"
  if [[ -z "$digest" ]]; then
    echo "::error::$label image not found in Artifact Registry: $image" >&2
    echo "  Build/push it first or remove --skip-build." >&2
    exit 1
  fi
  echo "$digest"
}

latest_created_revision() {
  service_value "$1" "status.latestCreatedRevisionName"
}

latest_ready_revision() {
  service_value "$1" "status.latestReadyRevisionName"
}

revision_json() {
  local svc="$1"
  local revision="$2"

  gcloud run revisions describe "$revision" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --format=json
}

revision_ready_state() {
  local label="$1"
  local revision="$2"
  local image="$3"
  local image_digest="$4"
  local env_name="$5"
  local expected_sha="$6"
  local svc="$7"
  local json_file
  local rc=0

  json_file="$(mktemp)"
  cat > "$json_file"
  "$PYTHON_BIN" - "$json_file" "$label" "$revision" "$image" "$image_digest" "$env_name" "$expected_sha" "$svc" <<'PY' || rc=$?
import json
import sys

json_file, label, revision, image, image_digest, env_name, expected_sha, svc = sys.argv[1:]
with open(json_file, encoding="utf-8") as fh:
    rev = json.load(fh)

image_digests = {
    item.strip()
    for item in image_digest.replace("\n", ",").split(",")
    if item.strip()
}

def extract_digest(value: str) -> str:
    if "@sha256:" in value:
        return value.rsplit("@", 1)[1]
    if value.startswith("sha256:"):
        return value
    return ""

revision_service = rev.get("metadata", {}).get("labels", {}).get(
    "serving.knative.dev/service", ""
)
if revision_service and revision_service != svc:
    print(
        f"{label} revision {revision} service mismatch: "
        f"expected {svc}; saw {revision_service}"
    )
    sys.exit(1)

conditions = rev.get("status", {}).get("conditions", []) or []
ready = next((item for item in conditions if item.get("type") == "Ready"), {})
ready_status = ready.get("status", "")
ready_reason = ready.get("reason", "")
ready_message = ready.get("message", "")

containers = rev.get("spec", {}).get("containers", []) or []
status_digest = rev.get("status", {}).get("imageDigest", "")
single_container_status_digest_matches = (
    len(containers) == 1
    and extract_digest(status_digest) in image_digests
)

def image_matches(candidate: str) -> bool:
    if candidate == image:
        return True
    candidate_digest = extract_digest(candidate)
    if candidate_digest and candidate_digest in image_digests:
        return True
    if single_container_status_digest_matches:
        return True
    return False

matched_containers = [
    container for container in containers if image_matches(container.get("image", ""))
]
if not matched_containers:
    seen_images = ", ".join(
        container.get("image", "<missing>") for container in containers
    ) or "<none>"
    print(
        f"{label} revision {revision} image mismatch: expected {image}"
        f" or digest(s) {image_digest or '<unknown>'}; saw {seen_images}"
    )
    sys.exit(1)

def env_value(container, key):
    for item in container.get("env", []) or []:
        if item.get("name") == key:
            return item.get("value")
    return None

commit_values = [
    env_value(container, env_name)
    for container in matched_containers
    if env_value(container, env_name) is not None
]
if expected_sha not in commit_values:
    safe_values = ", ".join(value or "<unset>" for value in commit_values) or "<missing>"
    print(
        f"{label} revision {revision} commit metadata mismatch: "
        f"{env_name} expected {expected_sha}; saw {safe_values}"
    )
    sys.exit(1)

details = (
    f"Ready={ready_status or '<missing>'}"
    f" reason={ready_reason or '<none>'}"
    f" message={ready_message or '<none>'}"
)
if ready_status == "True":
    print(
        f"Ready: {label} revision {revision} ({details}); "
        "image and commit metadata matched"
    )
    sys.exit(0)
if ready_status == "False":
    print(f"{label} revision {revision} is not ready ({details})")
    sys.exit(1)

print(f"{label} revision {revision} is still becoming ready ({details})")
sys.exit(2)
PY
  rm -f "$json_file"
  return "$rc"
}

wait_for_staged_revision_ready() {
  local svc="$1"
  local revision="$2"
  local label="$3"
  local image="$4"
  local image_digest="$5"
  local env_name="$6"
  local expected_sha="$7"
  local state_output=""
  local state_rc=0

  echo "Waiting for $label staged revision object to become ready: $revision"
  for attempt in $(seq 1 30); do
    set +e
    state_output="$(revision_json "$svc" "$revision" | revision_ready_state "$label" "$revision" "$image" "$image_digest" "$env_name" "$expected_sha" "$svc")"
    state_rc=$?
    set -e
    if [[ $state_rc -eq 0 ]]; then
      echo "$state_output"
      return 0
    fi
    if [[ $state_rc -eq 1 ]]; then
      echo "::error::$state_output" >&2
      return 1
    fi
    echo "  attempt $attempt: $state_output - retrying in 10s"
    sleep 10
  done

  echo "::error::$label staged revision object did not become ready: $revision" >&2
  return 1
}

update_service_no_traffic() {
  local result_var="$1"
  local svc="$2"
  local image="$3"
  local env_vars="$4"
  local label="$5"
  local image_digest="$6"
  local env_name="$7"
  local before
  local after

  before="$(latest_created_revision "$svc" || true)"
  run gcloud run services update "$svc" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --image="$image" \
    --update-env-vars="$env_vars" \
    --no-traffic

  after="$(latest_created_revision "$svc")"
  if [[ -z "$after" ]]; then
    echo "::error::Could not identify latest created revision for $label service '$svc'." >&2
    return 1
  fi

  echo "$label previous latest revision: ${before:-none}"
  echo "$label new staged revision    : $after"
  wait_for_staged_revision_ready "$svc" "$after" "$label" "$image" "$image_digest" "$env_name" "$DEPLOY_SHA"
  printf -v "$result_var" '%s' "$after"
}

set_probe_tag() {
  local svc="$1"
  local tag="$2"
  local revision="$3"

  echo "+ gcloud run services update-traffic $svc --set-tags=$tag=$revision"
  if gcloud run services update-traffic "$svc" \
      --project="$GCP_PROJECT_ID" \
      --region="$GCP_REGION" \
      --set-tags="$tag=$revision"; then
    return 0
  fi

  echo "::warning::Could not set Cloud Run probe tag '$tag' for $revision." >&2
  return 1
}

remove_probe_tag() {
  local svc="$1"
  local tag="$2"

  echo "+ gcloud run services update-traffic $svc --remove-tags=$tag"
  gcloud run services update-traffic "$svc" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --remove-tags="$tag" >/dev/null 2>&1 || true
}

move_traffic_to_revision() {
  local svc="$1"
  local revision="$2"
  local label="$3"

  run_may_fail gcloud run services update-traffic "$svc" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --to-revisions="$revision=100"
}

rollback_service_traffic() {
  local svc="$1"
  local traffic_spec="$2"
  local label="$3"

  if [[ -z "$traffic_spec" ]]; then
    echo "::warning::No previous $label traffic spec captured; cannot rollback automatically." >&2
    return 0
  fi

  echo "::warning::Rolling back $label traffic to: $traffic_spec" >&2
  run_may_fail gcloud run services update-traffic "$svc" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --to-revisions="$traffic_spec"
}

json_field_from_stdin() {
  local field="$1"
  local json_file
  local rc=0

  json_file="$(mktemp)"
  cat > "$json_file"
  "$PYTHON_BIN" - "$json_file" "$field" <<'PY' || rc=$?
import json
import sys

json_file, field = sys.argv[1:]
with open(json_file, encoding="utf-8") as fh:
    payload = json.load(fh)
print(payload.get(field, ""))
PY
  rm -f "$json_file"
  return "$rc"
}

poll_health_url() {
  local url="$1"
  local label="$2"
  local attempts="${3:-30}"
  local resp=""
  local status=""
  local commit=""

  echo "Polling $label health at $url for commit=$SHORT_SHA ..."
  for attempt in $(seq 1 "$attempts"); do
    resp="$(curl -fsS "$url" 2>/dev/null || true)"
    status="$(printf '%s' "$resp" | json_field_from_stdin status 2>/dev/null || true)"
    commit="$(printf '%s' "$resp" | json_field_from_stdin commit 2>/dev/null || true)"
    if [[ "$status" == "healthy" && "${commit:0:7}" == "$SHORT_SHA" ]]; then
      echo "Verified $label health: status=healthy commit=$commit"
      return 0
    fi
    echo "  attempt $attempt: status=${status:-?} commit=${commit:-?} (want $SHORT_SHA) - retrying in 10s"
    sleep 10
  done

  echo "::error::$label health did not converge to $SHORT_SHA." >&2
  echo "  Last response: $resp" >&2
  return 1
}

print_manual_traffic_commands() {
  local api_revision="$1"
  local ui_revision="$2"

  cat <<EOF
Manual traffic commands:
  gcloud run services update-traffic "$API_SERVICE" --project="$GCP_PROJECT_ID" --region="$GCP_REGION" --to-revisions="$api_revision=100"
  gcloud run services update-traffic "$UI_SERVICE" --project="$GCP_PROJECT_ID" --region="$GCP_REGION" --to-revisions="$ui_revision=100"
EOF
}

# 1. Resolve commit.
if [[ -z "$DEPLOY_SHA" ]]; then
  git fetch --quiet origin "${PROD_BRANCH#origin/}"
  DEPLOY_SHA="$(git rev-parse "$PROD_BRANCH")"
fi
SHORT_SHA="${DEPLOY_SHA:0:7}"

echo "--- Deploy plan ------------------------------------------------"
echo "  project     : $GCP_PROJECT_ID"
echo "  run region  : $GCP_REGION"
echo "  gar region  : $GAR_REGION"
echo "  registry    : $GAR_REGISTRY"
echo "  api svc     : $API_SERVICE"
echo "  ui svc      : $UI_SERVICE"
echo "  commit      : $DEPLOY_SHA ($SHORT_SHA)"
echo "  migrations  : $([[ $RUN_MIGRATIONS -eq 1 ]] && echo yes || echo no)"
echo "  build       : $([[ $SKIP_BUILD -eq 1 ]] && echo skip || echo yes)"
echo "  traffic     : $TRAFFIC_MODE"
echo "  dry-run     : $([[ $DRY_RUN -eq 1 ]] && echo yes || echo no)"
echo "----------------------------------------------------------------"

if [[ $DRY_RUN -eq 0 && $ASSUME_YES -ne 1 ]]; then
  if ! read -rp "Proceed? [y/N] " confirm; then
    echo "Aborted."
    exit 0
  fi
  confirm="${confirm//$'\r'/}"
  case "$confirm" in
    y|Y|yes|YES) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
fi

# 2. Sanity-check services and capture current traffic before writing.
for svc in "$API_SERVICE" "$UI_SERVICE"; do
  if ! gcloud run services describe "$svc" \
        --project="$GCP_PROJECT_ID" --region="$GCP_REGION" \
        --format="value(status.url)" >/dev/null 2>&1; then
    echo "::error::Cloud Run service '$svc' not found in $GCP_REGION/$GCP_PROJECT_ID." >&2
    echo "  Override with API_SERVICE/UI_SERVICE/GCP_REGION env vars." >&2
    exit 1
  fi
done

PREVIOUS_API_TRAFFIC_SUMMARY="$(traffic_summary "$API_SERVICE")"
PREVIOUS_UI_TRAFFIC_SUMMARY="$(traffic_summary "$UI_SERVICE")"
PREVIOUS_API_TRAFFIC_SPEC="$(traffic_to_revisions "$API_SERVICE")"
PREVIOUS_UI_TRAFFIC_SPEC="$(traffic_to_revisions "$UI_SERVICE")"
PREVIOUS_API_READY_REVISION="$(latest_ready_revision "$API_SERVICE" || true)"
PREVIOUS_UI_READY_REVISION="$(latest_ready_revision "$UI_SERVICE" || true)"

echo "Previous API ready revision: ${PREVIOUS_API_READY_REVISION:-unknown}"
echo "Previous UI ready revision : ${PREVIOUS_UI_READY_REVISION:-unknown}"
echo "Previous API traffic       : $PREVIOUS_API_TRAFFIC_SUMMARY"
echo "Previous UI traffic        : $PREVIOUS_UI_TRAFFIC_SUMMARY"

API_IMAGE="${GAR_REGISTRY}/agenticorg:${DEPLOY_SHA}"
UI_IMAGE="${GAR_REGISTRY}/agenticorg-ui-cloudrun:${DEPLOY_SHA}"
API_UPDATE_ENV_VARS="AGENTICORG_GIT_SHA=${DEPLOY_SHA},AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED=true"
UI_UPDATE_ENV_VARS="GIT_SHA=${DEPLOY_SHA}"

# 3. Build + push images.
if [[ $SKIP_BUILD -eq 0 ]]; then
  run gcloud auth configure-docker "$GAR_HOST" --quiet

  run docker build \
    -t "$API_IMAGE" \
    -t "${GAR_REGISTRY}/agenticorg:latest" \
    .
  run docker push "$API_IMAGE"
  run docker push "${GAR_REGISTRY}/agenticorg:latest"

  run docker build \
    -t "$UI_IMAGE" \
    -t "${GAR_REGISTRY}/agenticorg-ui-cloudrun:latest" \
    -f Dockerfile.ui.cloudrun .
  run docker push "$UI_IMAGE"
  run docker push "${GAR_REGISTRY}/agenticorg-ui-cloudrun:latest"
fi

if [[ $DRY_RUN -eq 1 ]]; then
  API_IMAGE_DIGEST="<not checked in dry-run>"
  UI_IMAGE_DIGEST="<not checked in dry-run>"
else
  API_IMAGE_DIGEST="$(require_image_digest "$API_IMAGE" "API")"
  UI_IMAGE_DIGEST="$(require_image_digest "$UI_IMAGE" "UI")"
fi
echo "API image tag/digest: $API_IMAGE @ $API_IMAGE_DIGEST"
echo "UI image tag/digest : $UI_IMAGE @ $UI_IMAGE_DIGEST"

# 4. Create migrate job (optional, idempotent-ish).
if [[ $CREATE_MIGRATE_JOB -eq 1 ]]; then
  if gcloud run jobs describe "$MIGRATE_JOB" \
       --project="$GCP_PROJECT_ID" --region="$GCP_REGION" >/dev/null 2>&1; then
    run gcloud run jobs update "$MIGRATE_JOB" \
      --project="$GCP_PROJECT_ID" \
      --region="$GCP_REGION" \
      --image="$API_IMAGE" \
      --command="python" \
      --args="scripts/alembic_migrate.py" \
      --set-env-vars="AGENTICORG_ENABLE_LEGACY_STARTUP_DDL=0"
  else
    run gcloud run jobs create "$MIGRATE_JOB" \
      --project="$GCP_PROJECT_ID" \
      --region="$GCP_REGION" \
      --image="$API_IMAGE" \
      --command="python" \
      --args="scripts/alembic_migrate.py" \
      --set-env-vars="AGENTICORG_ENABLE_LEGACY_STARTUP_DDL=0"
  fi
fi

# 5. Run migrations (optional).
if [[ $RUN_MIGRATIONS -eq 1 ]]; then
  if ! gcloud run jobs describe "$MIGRATE_JOB" \
        --project="$GCP_PROJECT_ID" --region="$GCP_REGION" >/dev/null 2>&1; then
    echo "::error::Migrate job '$MIGRATE_JOB' missing; re-run with --create-migrate-job first." >&2
    exit 1
  fi
  run gcloud run jobs update "$MIGRATE_JOB" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --image="$API_IMAGE"
  run gcloud run jobs execute "$MIGRATE_JOB" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --wait
fi

if [[ $DRY_RUN -eq 1 ]]; then
  API_NEW_REVISION="${API_SERVICE}-new-${SHORT_SHA}"
  UI_NEW_REVISION="${UI_SERVICE}-new-${SHORT_SHA}"
  echo "[dry-run] would update $API_SERVICE image/env with --no-traffic"
  echo "[dry-run] would update $UI_SERVICE image/env with --no-traffic"
  echo "[dry-run] staged API revision placeholder: $API_NEW_REVISION"
  echo "[dry-run] staged UI revision placeholder : $UI_NEW_REVISION"
  case "$TRAFFIC_MODE" in
    latest)
      echo "[dry-run] would check staged API/UI readiness from revision objects, not service latestReadyRevisionName"
      echo "[dry-run] would tag/probe $API_NEW_REVISION before traffic shift when Cloud Run returns a tagged URL"
      echo "[dry-run] would route API traffic 100% to $API_NEW_REVISION"
      echo "[dry-run] would stage and route UI traffic 100% to $UI_NEW_REVISION only after API health passes"
      ;;
    preserve)
      echo "[dry-run] NOT DEPLOYED: --traffic preserve would leave API/UI traffic unchanged"
      ;;
    manual)
      echo "[dry-run] NOT DEPLOYED: --traffic manual would leave API/UI traffic unchanged"
      print_manual_traffic_commands "$API_NEW_REVISION" "$UI_NEW_REVISION"
      ;;
  esac
  exit 0
fi

# 6. Stage API without moving traffic. In latest mode, UI is staged only after
# API public health passes so an API failure cannot leave a new UI revision live.
API_NEW_REVISION=""
UI_NEW_REVISION=""
update_service_no_traffic API_NEW_REVISION "$API_SERVICE" "$API_IMAGE" "$API_UPDATE_ENV_VARS" "API" "$API_IMAGE_DIGEST" "AGENTICORG_GIT_SHA"

echo "Staged API revision: $API_NEW_REVISION"

if [[ "$TRAFFIC_MODE" == "preserve" ]]; then
  update_service_no_traffic UI_NEW_REVISION "$UI_SERVICE" "$UI_IMAGE" "$UI_UPDATE_ENV_VARS" "UI" "$UI_IMAGE_DIGEST" "GIT_SHA"
  echo "Staged UI revision : $UI_NEW_REVISION"
  echo "NOT DEPLOYED: --traffic preserve staged revisions but left production traffic unchanged."
  echo "Current API traffic: $(traffic_summary "$API_SERVICE")"
  echo "Current UI traffic : $(traffic_summary "$UI_SERVICE")"
  exit 0
fi

if [[ "$TRAFFIC_MODE" == "manual" ]]; then
  update_service_no_traffic UI_NEW_REVISION "$UI_SERVICE" "$UI_IMAGE" "$UI_UPDATE_ENV_VARS" "UI" "$UI_IMAGE_DIGEST" "GIT_SHA"
  echo "Staged UI revision : $UI_NEW_REVISION"
  echo "NOT DEPLOYED: --traffic manual staged revisions but left production traffic unchanged."
  print_manual_traffic_commands "$API_NEW_REVISION" "$UI_NEW_REVISION"
  exit 0
fi

# 7. Directly probe the staged API revision when Cloud Run tag URLs are available.
API_PROBE_TAG="deploy-${SHORT_SHA}"
API_TAGGED_URL=""
if set_probe_tag "$API_SERVICE" "$API_PROBE_TAG" "$API_NEW_REVISION"; then
  API_TAGGED_URL="$(tagged_revision_url "$API_SERVICE" "$API_PROBE_TAG" "$API_NEW_REVISION" || true)"
  if [[ -n "$API_TAGGED_URL" ]]; then
    if ! poll_health_url "${API_TAGGED_URL%/}${API_HEALTH_PATH}" "staged API revision" 18; then
      remove_probe_tag "$API_SERVICE" "$API_PROBE_TAG"
      echo "::error::Staged API revision failed direct health probe; traffic was not moved." >&2
      exit 1
    fi
  else
    echo "::warning::Cloud Run did not return a tagged revision URL for $API_NEW_REVISION." >&2
    echo "::warning::API health cannot be verified until traffic is moved; continuing with rollback-protected shift." >&2
  fi
else
  echo "::warning::API health cannot be verified by direct revision URL; continuing with rollback-protected shift." >&2
fi

# 8. Move API first, verify public health, then move UI.
if ! move_traffic_to_revision "$API_SERVICE" "$API_NEW_REVISION" "API"; then
  remove_probe_tag "$API_SERVICE" "$API_PROBE_TAG"
  echo "::error::Failed to move API traffic to $API_NEW_REVISION." >&2
  rollback_service_traffic "$API_SERVICE" "$PREVIOUS_API_TRAFFIC_SPEC" "API"
  rollback_service_traffic "$UI_SERVICE" "$PREVIOUS_UI_TRAFFIC_SPEC" "UI"
  exit 1
fi

if ! poll_health_url "$HEALTH_URL" "public API" 30; then
  remove_probe_tag "$API_SERVICE" "$API_PROBE_TAG"
  echo "::error::Public API health failed after traffic shift; rolling back API/UI traffic." >&2
  rollback_service_traffic "$API_SERVICE" "$PREVIOUS_API_TRAFFIC_SPEC" "API"
  rollback_service_traffic "$UI_SERVICE" "$PREVIOUS_UI_TRAFFIC_SPEC" "UI"
  exit 1
fi

if ! update_service_no_traffic UI_NEW_REVISION "$UI_SERVICE" "$UI_IMAGE" "$UI_UPDATE_ENV_VARS" "UI" "$UI_IMAGE_DIGEST" "GIT_SHA"; then
  remove_probe_tag "$API_SERVICE" "$API_PROBE_TAG"
  echo "::error::Failed to stage UI revision after API verification; rolling back API/UI traffic." >&2
  rollback_service_traffic "$API_SERVICE" "$PREVIOUS_API_TRAFFIC_SPEC" "API"
  rollback_service_traffic "$UI_SERVICE" "$PREVIOUS_UI_TRAFFIC_SPEC" "UI"
  exit 1
fi
echo "Staged UI revision : $UI_NEW_REVISION"

if ! move_traffic_to_revision "$UI_SERVICE" "$UI_NEW_REVISION" "UI"; then
  remove_probe_tag "$API_SERVICE" "$API_PROBE_TAG"
  echo "::error::Failed to move UI traffic to $UI_NEW_REVISION; rolling back API/UI traffic." >&2
  rollback_service_traffic "$API_SERVICE" "$PREVIOUS_API_TRAFFIC_SPEC" "API"
  rollback_service_traffic "$UI_SERVICE" "$PREVIOUS_UI_TRAFFIC_SPEC" "UI"
  exit 1
fi

remove_probe_tag "$API_SERVICE" "$API_PROBE_TAG"

echo "New API traffic: $(traffic_summary "$API_SERVICE")"
echo "New UI traffic : $(traffic_summary "$UI_SERVICE")"
echo "DEPLOYED: API=$API_NEW_REVISION UI=$UI_NEW_REVISION commit=$DEPLOY_SHA"
