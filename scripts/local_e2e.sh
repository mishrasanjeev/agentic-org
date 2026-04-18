#!/usr/bin/env bash
# local_e2e.sh — run the Playwright suite against a fully local stack.
#
# Motivation:
#   Our CI e2e job only runs on main post-deploy (~25 min round trip). This
#   script gives the same coverage on a developer workstation with Docker
#   in ~3 min for a single spec, ~5 min for the whole suite. Catches
#   regressions before they land on main.
#
# What it does:
#   1. docker compose up postgres + redis + minio + api + worker.
#   2. Wait for the API /health endpoint.
#   3. POST /auth/login as the demo CEO to mint an E2E token.
#   4. Seed demo agents via scripts/seed_e2e_demo_agents.py.
#   5. Start the UI dev server (vite, which proxies /api to :8000).
#   6. Wait for the UI to respond.
#   7. Run Playwright with BASE_URL pointing at the local UI.
#   8. Tear down UI + compose on exit.
#
# Usage:
#   bash scripts/local_e2e.sh                      # full suite
#   bash scripts/local_e2e.sh ui/e2e/session5-bugs.spec.ts   # one spec
#   SKIP_BUILD=1 bash scripts/local_e2e.sh         # reuse existing UI deps
#   KEEP_UP=1   bash scripts/local_e2e.sh          # leave stack running
#
# Env:
#   KEEP_UP=1       # do not docker compose down on exit
#   SKIP_BUILD=1    # skip `npm install` / `playwright install`
#   LOCAL_UI_PORT   # default 5173 (vite default)
#   LOCAL_API_PORT  # default 8000

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOCAL_UI_PORT="${LOCAL_UI_PORT:-5173}"
LOCAL_API_PORT="${LOCAL_API_PORT:-8000}"
UI_URL="http://localhost:${LOCAL_UI_PORT}"
API_URL="http://localhost:${LOCAL_API_PORT}"
SPEC_ARG="${1:-}"

# Demo creds match core/seed_ca_demo.py — init_db seeds these on API boot.
DEMO_EMAIL="ceo@agenticorg.local"
DEMO_PASSWORD="ceo123!"

RED='\033[0;31m'
GRN='\033[0;32m'
YEL='\033[0;33m'
BLU='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${BLU}[local_e2e]${NC} $*"; }
ok()   { echo -e "${GRN}[local_e2e] ✓${NC} $*"; }
warn() { echo -e "${YEL}[local_e2e] !${NC} $*"; }
fail() { echo -e "${RED}[local_e2e] ✗${NC} $*" >&2; }

UI_PID=""

cleanup() {
  local exit_code=$?
  if [[ -n "$UI_PID" ]] && kill -0 "$UI_PID" 2>/dev/null; then
    log "Stopping UI dev server (pid=$UI_PID)"
    kill "$UI_PID" 2>/dev/null || true
    wait "$UI_PID" 2>/dev/null || true
  fi
  if [[ "${KEEP_UP:-0}" != "1" ]]; then
    log "Tearing down docker compose"
    docker compose down --remove-orphans >/dev/null 2>&1 || true
  else
    log "KEEP_UP=1 — leaving docker compose running"
  fi
  if [[ $exit_code -eq 0 ]]; then
    ok "local e2e PASS"
  else
    fail "local e2e FAIL (exit $exit_code)"
  fi
  exit $exit_code
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# 0. Prerequisites
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  fail "docker is not installed or not on PATH"
  exit 2
fi
if ! docker compose version >/dev/null 2>&1; then
  fail "docker compose v2 is required"
  exit 2
fi
if ! command -v npx >/dev/null 2>&1; then
  fail "npx (Node.js) is required"
  exit 2
fi

# ---------------------------------------------------------------------------
# 1. Bring up the backend stack
# ---------------------------------------------------------------------------
log "docker compose up postgres redis minio api worker"
docker compose up -d postgres redis minio api worker

# ---------------------------------------------------------------------------
# 2. Wait for API /health
# ---------------------------------------------------------------------------
log "Waiting for API at ${API_URL}/api/v1/health (timeout 120s)"
deadline=$(( $(date +%s) + 120 ))
while :; do
  if curl -fsS "${API_URL}/api/v1/health" >/dev/null 2>&1; then
    ok "API is healthy"
    break
  fi
  if [[ $(date +%s) -ge $deadline ]]; then
    fail "API did not become healthy in 120s"
    docker compose logs --tail=50 api
    exit 3
  fi
  sleep 3
done

# ---------------------------------------------------------------------------
# 3. Mint E2E token
# ---------------------------------------------------------------------------
log "Logging in as ${DEMO_EMAIL} to mint E2E_TOKEN"
TOKEN=$(curl -fsS -X POST "${API_URL}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${DEMO_EMAIL}\",\"password\":\"${DEMO_PASSWORD}\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")
if [[ -z "$TOKEN" ]]; then
  fail "Could not obtain E2E_TOKEN — demo user likely not seeded. Check docker compose logs api."
  docker compose logs --tail=30 api
  exit 4
fi
ok "E2E_TOKEN obtained (${#TOKEN} chars)"

# ---------------------------------------------------------------------------
# 4. Seed demo agents (best effort — don't fail the run if seeding partials)
# ---------------------------------------------------------------------------
if [[ -f scripts/seed_e2e_demo_agents.py ]]; then
  log "Seeding demo agents"
  python3 scripts/seed_e2e_demo_agents.py \
    --base-url "${API_URL}" \
    --token "${TOKEN}" || warn "seed_e2e_demo_agents.py returned non-zero (continuing)"
fi

# ---------------------------------------------------------------------------
# 5. UI dev server (proxy /api -> :8000, serves at :5173)
# ---------------------------------------------------------------------------
if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  log "Installing UI deps (set SKIP_BUILD=1 to skip)"
  (cd ui && npm install --legacy-peer-deps --silent)
  log "Installing Playwright browsers"
  (cd ui && npx playwright install --with-deps chromium)
fi

log "Starting UI dev server on ${UI_URL}"
(cd ui && npm run dev -- --port "${LOCAL_UI_PORT}" --strictPort) >/tmp/local_e2e_ui.log 2>&1 &
UI_PID=$!

log "Waiting for UI at ${UI_URL} (timeout 60s)"
deadline=$(( $(date +%s) + 60 ))
while :; do
  if curl -fsS "${UI_URL}" >/dev/null 2>&1; then
    ok "UI is serving"
    break
  fi
  if [[ $(date +%s) -ge $deadline ]]; then
    fail "UI did not start in 60s. Last 30 lines:"
    tail -n 30 /tmp/local_e2e_ui.log || true
    exit 5
  fi
  sleep 2
done

# ---------------------------------------------------------------------------
# 6. Playwright
# ---------------------------------------------------------------------------
log "Running Playwright (BASE_URL=${UI_URL}${SPEC_ARG:+ spec=${SPEC_ARG}})"
(
  cd ui
  BASE_URL="${UI_URL}" \
  E2E_TOKEN="${TOKEN}" \
  MARKETING_URL="${UI_URL}" \
  npx playwright test ${SPEC_ARG:+"../${SPEC_ARG}"}
)

ok "Playwright run finished"
