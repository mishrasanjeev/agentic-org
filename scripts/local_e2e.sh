#!/usr/bin/env bash
# local_e2e.sh — run the Playwright suite against a fully local stack.
#
# Motivation:
#   Our CI e2e job only runs on main post-deploy (~25 min round trip). This
#   script gives the same coverage on a developer workstation in ~3 min for
#   a single spec. Catches regressions before they land on main.
#
# Design choice:
#   We DO NOT use the `api` service from docker-compose.yml — that builds
#   the full Dockerfile which installs torch / scipy / pandas (~1-2 GB of
#   ML deps, 30-60 min first build). Instead we run the API on the host
#   via uvicorn, reusing the already-installed `.venv`. docker compose is
#   only used for the stateful services (postgres + redis + minio).
#
# What it does:
#   1. docker compose up postgres + redis + minio (fast, ~30 s first time)
#   2. Wait for postgres health
#   3. Launch `uvicorn api.main:app` on host via the repo's .venv
#   4. Wait for the API /health endpoint
#   5. POST /auth/login as the demo partner to mint an E2E token
#   6. Seed demo agents via scripts/seed_e2e_demo_agents.py
#   7. Start the UI dev server (vite, proxies /api to :8000)
#   8. Wait for the UI to respond
#   9. Run Playwright with BASE_URL pointing at the local UI
#  10. Tear down UI + uvicorn + compose on exit
#
# Usage:
#   bash scripts/local_e2e.sh                                # full suite
#   bash scripts/local_e2e.sh ui/e2e/session5-bugs.spec.ts   # one spec
#   bash scripts/local_e2e.sh ui/e2e/a.spec.ts ui/e2e/b.spec.ts
#   SKIP_BUILD=1 bash scripts/local_e2e.sh                   # reuse ui/node_modules
#   KEEP_UP=1   bash scripts/local_e2e.sh                    # leave services running
#   LOCAL_UI_MODE=dev bash scripts/local_e2e.sh              # use Vite dev server
#
# Env:
#   KEEP_UP=1       # do not docker compose down / kill api on exit
#   RESET=1         # docker compose down -v first (nuke pgdata/redisdata)
#                   # — useful if a prior compose run baked in bad creds
#   SKIP_BUILD=1    # skip `npm install` / `playwright install`
#   LOCAL_UI_PORT   # optional: force a specific UI port; otherwise auto-pick
#   LOCAL_API_PORT  # default 8000
#   LOCAL_E2E_WORKERS # default 1; raise only when the local stack is stable
#   LOCAL_UI_MODE   # default preview; use dev only for frontend iteration
#   SKIP_UI_BUILD   # preview mode only: set 1 to reuse ui/dist
#   PYTHON_BIN      # default: $REPO/.venv/Scripts/python.exe (Windows)
#                   #          or  $REPO/.venv/bin/python (Linux/macOS)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

REQUESTED_LOCAL_UI_PORT="${LOCAL_UI_PORT:-}"
LOCAL_API_PORT="${LOCAL_API_PORT:-8000}"
LOCAL_E2E_WORKERS="${LOCAL_E2E_WORKERS:-1}"
LOCAL_UI_MODE="${LOCAL_UI_MODE:-preview}"
API_URL="http://localhost:${LOCAL_API_PORT}"
SPEC_ARGS=("$@")

# Demo creds match core/seed_ca_demo.py DEMO_USER_EMAIL / DEMO_USER_PASSWORD.
# The CA-firms seeder is what runs in our bootstrap step below. It creates
# this primary demo partner plus the documented CxO role accounts used by
# the wider regression suite.
DEMO_EMAIL="demo@cafirm.agenticorg.ai"
DEMO_PASSWORD="demo123!"

RED='\033[0;31m'
GRN='\033[0;32m'
YEL='\033[0;33m'
BLU='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${BLU}[local_e2e]${NC} $*"; }
ok()   { echo -e "${GRN}[local_e2e] ✓${NC} $*"; }
warn() { echo -e "${YEL}[local_e2e] !${NC} $*"; }
fail() { echo -e "${RED}[local_e2e] ✗${NC} $*" >&2; }

# Locate the repo's Python venv (Windows vs POSIX).
if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x "${REPO_ROOT}/.venv/Scripts/python.exe" ]]; then
    PYTHON_BIN="${REPO_ROOT}/.venv/Scripts/python.exe"
  elif [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
  else
    fail "No .venv found. Create one with the repo's Python deps installed, or set PYTHON_BIN."
    exit 2
  fi
fi

API_PID=""
UI_PID=""

# Declared here so the cleanup trap can reference it even on early exits.
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.local-e2e.yml)

stop_pid() {
  local pid="$1"
  local label="$2"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    return
  fi

  log "Stopping ${label} (pid=${pid})"
  kill "$pid" 2>/dev/null || true
  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      return
    fi
    sleep 0.25
  done

  warn "${label} did not exit after SIGTERM; forcing pid=${pid}"
  if command -v taskkill >/dev/null 2>&1; then
    taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
  else
    kill -9 "$pid" 2>/dev/null || true
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid" 2>/dev/null || true
  fi
}

cleanup() {
  local exit_code=$?
  stop_pid "$UI_PID" "UI server"
  stop_pid "$API_PID" "API uvicorn"
  if [[ "${KEEP_UP:-0}" != "1" ]]; then
    log "Tearing down docker compose (postgres/redis/minio)"
    docker compose "${COMPOSE_FILES[@]}" down --remove-orphans >/dev/null 2>&1 \
      || docker compose down --remove-orphans >/dev/null 2>&1 || true
  else
    log "KEEP_UP=1 — leaving services running"
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
# 1. Bring up the stateful services on ALT host ports via a compose override
#
# We use docker-compose.local-e2e.yml to remap 5432→5433, 6379→6380,
# 9000→9002. This avoids collisions with any Postgres / Redis / MinIO the
# developer already has bound on the standard ports, and guarantees the
# API connects to the compose-managed services (with known creds) rather
# than accidentally talking to a host Postgres that lacks the agenticorg
# user.
# ---------------------------------------------------------------------------
export COMPOSE_PROJECT_NAME=agenticorg_local_e2e

tcp_listening() {
  local host="$1"
  local port="$2"
  (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null
  local rc=$?
  exec 3<&- 2>/dev/null || true
  exec 3>&- 2>/dev/null || true
  return $rc
}

choose_free_local_port() {
  "$PYTHON_BIN" - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

if [[ -n "$REQUESTED_LOCAL_UI_PORT" ]]; then
  LOCAL_UI_PORT="$REQUESTED_LOCAL_UI_PORT"
  UI_URL="http://localhost:${LOCAL_UI_PORT}"
  if tcp_listening localhost "$LOCAL_UI_PORT"; then
    fail "LOCAL_UI_PORT=${LOCAL_UI_PORT} is already in use. Stop that process or unset LOCAL_UI_PORT so this script can choose a free port."
    exit 2
  fi
else
  LOCAL_UI_PORT="$(choose_free_local_port)"
  UI_URL="http://localhost:${LOCAL_UI_PORT}"
  log "LOCAL_UI_PORT unset; selected free UI port ${LOCAL_UI_PORT}"
fi

API_LOG="/tmp/local_e2e_api_${LOCAL_API_PORT}.log"
UI_LOG="/tmp/local_e2e_ui_${LOCAL_UI_PORT}.log"

# Read "0.0.0.0:NNNNN" from `docker compose port <svc> <container-port>`
# and return just NNNNN. Windows prints CR so we strip it.
compose_port() {
  local svc="$1"
  local cport="$2"
  docker compose "${COMPOSE_FILES[@]}" port "$svc" "$cport" 2>/dev/null \
    | awk -F: '{print $NF}' | tr -d '\r\n'
}

clear_e2e_redis_auth_state() {
  log "Clearing volatile e2e auth/session state from Redis"
  docker compose "${COMPOSE_FILES[@]}" exec -T redis sh -c '
set -eu
for pattern in "auth:blocked:*" "auth:failures:*" "auth:login_attempts:*" "auth:signup:*" "token_blacklist:*"; do
  redis-cli --scan --pattern "$pattern" | while IFS= read -r key; do
    if [ -n "$key" ]; then
      redis-cli DEL "$key" >/dev/null
    fi
  done
done
' >/dev/null || warn "Could not clear e2e Redis auth/session keys; continuing"
}

if [[ "${RESET:-0}" == "1" ]]; then
  log "RESET=1 — tearing down any prior compose + removing pgdata/redisdata/miniodata volumes"
  docker compose "${COMPOSE_FILES[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
fi

log "docker compose up -d postgres redis minio (project=${COMPOSE_PROJECT_NAME}, Docker-assigned host ports)"
docker compose "${COMPOSE_FILES[@]}" up -d postgres redis minio

PG_PORT="$(compose_port postgres 5432)"
REDIS_PORT="$(compose_port redis 6379)"
MINIO_PORT="$(compose_port minio 9000)"
if [[ -z "$PG_PORT" || -z "$REDIS_PORT" || -z "$MINIO_PORT" ]]; then
  fail "Could not read host ports from docker compose. pg='${PG_PORT}' redis='${REDIS_PORT}' minio='${MINIO_PORT}'"
  docker compose "${COMPOSE_FILES[@]}" ps
  exit 3
fi
ok "host ports assigned — pg=${PG_PORT}, redis=${REDIS_PORT}, minio=${MINIO_PORT}"

log "Waiting for redis ready (PING, timeout 30s)"
deadline=$(( $(date +%s) + 30 ))
while :; do
  if [[ "$(docker compose "${COMPOSE_FILES[@]}" exec -T redis redis-cli ping 2>/dev/null | tr -d '\r')" == "PONG" ]]; then
    ok "redis is ready (PING)"
    break
  fi
  if [[ $(date +%s) -ge $deadline ]]; then
    fail "redis did not become ready in 30s"
    docker compose "${COMPOSE_FILES[@]}" logs --tail=40 redis 2>/dev/null || true
    exit 3
  fi
  sleep 1
done
clear_e2e_redis_auth_state

log "Waiting for postgres ready (pg_isready, timeout 90s)"
deadline=$(( $(date +%s) + 90 ))
while :; do
  # pg_isready inside the container — avoids Docker Desktop's
  # TCP-accept-before-ready race where tcp_listening succeeds but the
  # subsequent asyncpg connect hits WinError 1225.
  if docker compose "${COMPOSE_FILES[@]}" exec -T postgres pg_isready -U agenticorg -d agenticorg >/dev/null 2>&1; then
    ok "postgres is ready (pg_isready)"
    break
  fi
  if [[ $(date +%s) -ge $deadline ]]; then
    fail "postgres did not become ready in 90s"
    docker compose "${COMPOSE_FILES[@]}" logs --tail=40 postgres 2>/dev/null || true
    exit 3
  fi
  sleep 2
done

# Extra belt-and-braces: confirm an asyncpg-style connect succeeds from
# the host, not just inside the container. On Docker Desktop Windows the
# port forwarder briefly accepts TCP before upstream is ready, so even
# after pg_isready we wait for one real end-to-end handshake.
log "Confirming postgres is reachable from host on :${PG_PORT}"
deadline=$(( $(date +%s) + 30 ))
while :; do
  if "$PYTHON_BIN" -c "
import asyncio, sys
async def go():
    import asyncpg
    c = await asyncpg.connect(
        host='localhost', port=${PG_PORT},
        user='agenticorg', password='agenticorg_dev', database='agenticorg',
    )
    await c.close()
asyncio.run(go())
" >/dev/null 2>&1; then
    ok "postgres accepts host-side asyncpg connect"
    break
  fi
  if [[ $(date +%s) -ge $deadline ]]; then
    fail "postgres host-side connect failed for 30s after pg_isready"
    exit 3
  fi
  sleep 2
done

# ---------------------------------------------------------------------------
# 2. Launch uvicorn on host (reuses .venv — no docker build)
# ---------------------------------------------------------------------------
export AGENTICORG_ENV=development
export AGENTICORG_DB_URL="postgresql+asyncpg://agenticorg:agenticorg_dev@localhost:${PG_PORT}/agenticorg"
export AGENTICORG_REDIS_URL="redis://localhost:${REDIS_PORT}/0"
export AGENTICORG_STORAGE_BUCKET="agenticorg-docs-dev"
export AGENTICORG_STORAGE_ENDPOINT="http://localhost:${MINIO_PORT}"
export AGENTICORG_SECRET_KEY="dev-secret-key-change-in-production-32chars"
export AGENTICORG_PII_MASKING="true"

# ---------------------------------------------------------------------------
# Bootstrap schema: create base ORM tables + run alembic up to head.
#
# This mirrors what tests/integration/test_alembic_e2e.py does to stage
# a legacy-shaped DB before the alembic chain takes over. Without this,
# app startup verifies Alembic state and does not run schema DDL unless
# the local-only repair flag is explicitly enabled. Keep that flag unset
# here so Alembic owns the schema.
# ---------------------------------------------------------------------------
unset AGENTICORG_ENABLE_LEGACY_STARTUP_DDL

log "Bootstrapping schema (ORM metadata.create_all + alembic stamp + upgrade)"
"$PYTHON_BIN" -c "
import core.models  # noqa: F401 — register every model
from sqlalchemy import create_engine
from core.config import settings
from core.models.base import BaseModel
sync = settings.db_url.replace('+asyncpg', '').replace('postgresql+asyncpg', 'postgresql')
engine = create_engine(sync)
BaseModel.metadata.create_all(engine)
engine.dispose()
print('ORM create_all complete')
" || { fail "ORM metadata.create_all failed"; exit 4; }

"$PYTHON_BIN" scripts/alembic_migrate.py || { fail "alembic_migrate.py failed"; exit 4; }
ok "schema bootstrap complete"

# Seed the CA demo tenant + documented demo users. In the
# normal API startup path can skip demo seed when schema verification is
# the only startup action, so call the seeder directly here.
log "Seeding CA demo tenant + users"
"$PYTHON_BIN" -c "
import asyncio
from core.database import async_session_factory
from core.seed_ca_demo import seed_ca_demo

async def main():
    async with async_session_factory() as session:
        await seed_ca_demo(session)
        await session.commit()
    print('seed_ca_demo complete')

asyncio.run(main())
" || { fail "seed_ca_demo failed"; exit 4; }

# On Windows, bash trap sometimes fails to propagate to uvicorn children
# from an earlier interrupted run — the zombie process keeps :8000 bound.
# If anything's listening on our API port right now, abort with a clear
# message rather than trying to bind and getting a cryptic asyncio error.
if tcp_listening localhost "$LOCAL_API_PORT"; then
  fail "Port ${LOCAL_API_PORT} is already in use. A previous local_e2e run likely leaked a uvicorn child."
  fail "Find + kill the pid. Windows:"
  fail "  netstat -ano | grep ':${LOCAL_API_PORT} '"
  fail "  powershell.exe -Command 'Stop-Process -Id <pid> -Force'"
  exit 4
fi

log "Starting API (uvicorn) on :${LOCAL_API_PORT}"
"$PYTHON_BIN" -m uvicorn api.main:app \
  --host 127.0.0.1 --port "$LOCAL_API_PORT" \
  >"${API_LOG}" 2>&1 &
API_PID=$!

log "Waiting for API at ${API_URL}/api/v1/health (timeout 60s)"
deadline=$(( $(date +%s) + 60 ))
while :; do
  if curl -fsS "${API_URL}/api/v1/health" >/dev/null 2>&1; then
    ok "API is healthy"
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    fail "API process died during startup. Last 40 lines:"
    tail -n 40 "${API_LOG}" || true
    exit 4
  fi
  if [[ $(date +%s) -ge $deadline ]]; then
    fail "API did not become healthy in 60s. Last 40 lines:"
    tail -n 40 "${API_LOG}" || true
    exit 4
  fi
  sleep 2
done

# ---------------------------------------------------------------------------
# 3. Mint E2E token
# ---------------------------------------------------------------------------
log "Logging in as ${DEMO_EMAIL} to mint E2E_TOKEN"
TOKEN=$(curl -fsS -X POST "${API_URL}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${DEMO_EMAIL}\",\"password\":\"${DEMO_PASSWORD}\"}" \
  | "$PYTHON_BIN" -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")
if [[ -z "$TOKEN" ]]; then
  fail "Could not obtain E2E_TOKEN — demo user likely not seeded. Check ${API_LOG}."
  tail -n 20 "${API_LOG}" || true
  exit 5
fi
ok "E2E_TOKEN obtained (${#TOKEN} chars)"

# ---------------------------------------------------------------------------
# 4. Seed demo agents (best effort — don't fail the run if seeding partials)
# ---------------------------------------------------------------------------
if [[ -f scripts/seed_e2e_demo_agents.py ]]; then
  log "Seeding demo agents"
  "$PYTHON_BIN" scripts/seed_e2e_demo_agents.py \
    --base-url "${API_URL}" \
    --token "${TOKEN}" || warn "seed_e2e_demo_agents.py returned non-zero (continuing)"
fi

# ---------------------------------------------------------------------------
# 5. UI server (production preview by default; dev mode available for iteration)
# ---------------------------------------------------------------------------
if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  if [[ ! -d ui/node_modules ]]; then
    log "Installing UI deps (set SKIP_BUILD=1 to skip next time)"
    (cd ui && npm install --legacy-peer-deps --silent)
  else
    log "ui/node_modules exists — skipping npm install"
  fi
  log "Ensuring Playwright chromium is installed"
  (cd ui && npx playwright install --with-deps chromium)
fi

if [[ "$LOCAL_UI_MODE" == "preview" ]]; then
  if [[ "${SKIP_UI_BUILD:-0}" != "1" || ! -d ui/dist ]]; then
    log "Building UI for preview"
    (cd ui && npm run build)
  else
    log "SKIP_UI_BUILD=1 and ui/dist exists; reusing existing preview build"
  fi
  log "Starting UI preview server on ${UI_URL}"
  (cd ui && npm run preview -- --host 127.0.0.1 --port "${LOCAL_UI_PORT}" --strictPort) >"${UI_LOG}" 2>&1 &
elif [[ "$LOCAL_UI_MODE" == "dev" ]]; then
  log "Starting UI dev server on ${UI_URL}"
  (cd ui && npm run dev -- --host 127.0.0.1 --port "${LOCAL_UI_PORT}" --strictPort) >"${UI_LOG}" 2>&1 &
else
  fail "Unsupported LOCAL_UI_MODE='${LOCAL_UI_MODE}'. Use 'preview' or 'dev'."
  exit 6
fi
UI_PID=$!

log "Waiting for UI at ${UI_URL} (timeout 60s)"
deadline=$(( $(date +%s) + 60 ))
while :; do
  if ! kill -0 "$UI_PID" 2>/dev/null; then
    fail "UI process died during startup. Last 30 lines:"
    tail -n 30 "${UI_LOG}" || true
    exit 6
  fi
  if curl -fsS "${UI_URL}" >/dev/null 2>&1; then
    ok "UI is serving"
    break
  fi
  if [[ $(date +%s) -ge $deadline ]]; then
    fail "UI did not start in 60s. Last 30 lines:"
    tail -n 30 "${UI_LOG}" || true
    exit 6
  fi
  sleep 2
done

# ---------------------------------------------------------------------------
# 6. Playwright
# ---------------------------------------------------------------------------
log "Running Playwright (BASE_URL=${UI_URL}, workers=${LOCAL_E2E_WORKERS}${SPEC_ARGS[*]:+ specs=${SPEC_ARGS[*]}})"
(
  cd ui
  PLAYWRIGHT_ARGS=()
  for spec in "${SPEC_ARGS[@]}"; do
    PLAYWRIGHT_ARGS+=("../${spec}")
  done
  PLAYWRIGHT_ARGS+=("--workers=${LOCAL_E2E_WORKERS}")
  BASE_URL="${UI_URL}" \
  API_URL="${API_URL}" \
  E2E_TOKEN="${TOKEN}" \
  MARKETING_URL="${UI_URL}" \
  npx playwright test "${PLAYWRIGHT_ARGS[@]}"
)

ok "Playwright run finished"
