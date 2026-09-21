#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Browser end-to-end run for `make e2e`, executed inside the official
# Playwright image on the dev stack's network (see the e2e service in
# docker-compose.dev.yml).
#
#   scripts/run_e2e.sh [PLAYWRIGHT_CONFIG] [extra playwright args...]
#
# Installs the console's locked dependencies into a container-only
# node_modules volume, runs the suite against BASE_URL and hands the report
# and test artefacts back to the host user. Exits with Playwright's status.
set -euo pipefail

config="${1:-e2e/dev-stack.config.ts}"
shift || true

: "${BASE_URL:?BASE_URL must point at the console}"
cd "$(dirname "${BASH_SOURCE[0]}")/../ui"

installed="$(node -p "require('@playwright/test/package.json').version" 2>/dev/null || true)"
expected="$(node -p "require('./package-lock.json').packages['node_modules/@playwright/test'].version")"
if [[ "$installed" != "$expected" ]]; then
  npm ci --no-audit --no-fund --ignore-scripts
fi

status=0
npx playwright test --config="$config" "$@" || status=$?

if [[ -n "${HOST_UID:-}" && "${HOST_UID}" != "0" ]]; then
  for dir in test-results playwright-report ../docs/console/images; do
    if [[ -e "$dir" ]]; then
      chown -R "${HOST_UID}:${HOST_GID:-$HOST_UID}" "$dir"
    fi
  done
fi
exit "$status"
