#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Smoke test for the local development stack (docker-compose.dev.yml).
#
# Asserts that the API answers its liveness endpoint directly, that the console
# serves its health page, that the console proxies /api to the API, that the
# mock verification provider service answers, that the OIDC stub publishes its
# discovery document, that the model stub lists its scripted models, that the
# Grantex auth service is healthy and publishes its signing keys, and that the
# API container reaches Grantex with the configured URL and developer key.
# Fails with the failing check and response when any check does not pass.
#
# COMPOSE is the compose command for the stack (make passes its own); the last
# check runs inside the api container through it.
set -euo pipefail

api="http://127.0.0.1:${AGENTICORG_DEV_API_PORT:-8000}"
ui="http://127.0.0.1:${AGENTICORG_DEV_UI_PORT:-3000}"
mock_provider="http://127.0.0.1:${AGENTICORG_DEV_MOCK_PROVIDER_PORT:-8081}"
oidc="http://127.0.0.1:${AGENTICORG_DEV_OIDC_PORT:-9400}"
model="http://127.0.0.1:${AGENTICORG_DEV_MODEL_STUB_PORT:-8090}"
grantex="http://127.0.0.1:${AGENTICORG_DEV_GRANTEX_PORT:-3001}"
compose="${COMPOSE:-docker compose -f docker-compose.dev.yml}"
attempts="${SMOKE_ATTEMPTS:-60}"

check() {
  local name="$1" url="$2" expect="$3" body=""
  for _ in $(seq 1 "$attempts"); do
    if body="$(curl -fsS --max-time 5 "$url" 2>/dev/null)" && grep -q "$expect" <<<"$body"; then
      echo "ok   ${name}  ${url}"
      return 0
    fi
    sleep 2
  done
  echo "FAIL ${name}  ${url}: expected '${expect}', got: ${body:-<no response>}" >&2
  return 1
}

check "api liveness"                  "$api/api/v1/health/liveness" '"status": *"alive"'
check "api readiness (db + redis)"    "$api/api/v1/health"          '"status": *"healthy"'
check "console health"                "$ui/health"                  '^ok$'
check "console -> api proxy"          "$ui/api/v1/health/liveness"  '"status": *"alive"'
check "mock verification provider"    "$mock_provider/healthz"      '"alive": *true'
check "oidc stub discovery"           "$oidc/.well-known/openid-configuration" '"issuer": *"http://127.0.0.1:'
check "model stub scripted models"    "$model/v1/models"            '"scripted/final-only"'
check "grantex health (db + redis)"   "$grantex/health"             '"status": *"healthy"'
check "grantex signing keys"          "$grantex/.well-known/jwks.json" '"keys": *\[{'

# From inside the api container, with the API's own GRANTEX_BASE_URL and
# GRANTEX_API_KEY: the keys are reachable and the developer key is accepted.
api_to_grantex='
import json, os, sys, urllib.request
base, key = os.environ["GRANTEX_BASE_URL"].rstrip("/"), os.environ["GRANTEX_API_KEY"]
keys = json.load(urllib.request.urlopen(base + "/.well-known/jwks.json", timeout=5))["keys"]
request = urllib.request.Request(base + "/v1/agents", headers={"Authorization": "Bearer " + key})
status = urllib.request.urlopen(request, timeout=5).status
print(f"{len(keys)} signing key(s), developer key accepted (HTTP {status})")
sys.exit(0 if keys and status == 200 else 1)
'
if result="$($compose exec -T api python -c "$api_to_grantex" 2>&1)"; then
  echo "ok   api -> grantex  ${result}"
else
  echo "FAIL api -> grantex: ${result}" >&2
  exit 1
fi
echo "dev stack smoke test: ok"
