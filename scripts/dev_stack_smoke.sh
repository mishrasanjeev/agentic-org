#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Smoke test for the local development stack (docker-compose.dev.yml).
#
# Asserts that the API answers its liveness endpoint directly, that the console
# serves its health page, that the console proxies /api to the API, that the
# mock verification provider service answers, that the OIDC stub publishes its
# discovery document and that the model stub lists its scripted models. Fails
# with the failing URL and response when any check does not pass.
set -euo pipefail

api="http://127.0.0.1:${AGENTICORG_DEV_API_PORT:-8000}"
ui="http://127.0.0.1:${AGENTICORG_DEV_UI_PORT:-3000}"
mock_provider="http://127.0.0.1:${AGENTICORG_DEV_MOCK_PROVIDER_PORT:-8081}"
oidc="http://127.0.0.1:${AGENTICORG_DEV_OIDC_PORT:-9400}"
model="http://127.0.0.1:${AGENTICORG_DEV_MODEL_STUB_PORT:-8090}"
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
echo "dev stack smoke test: ok"
