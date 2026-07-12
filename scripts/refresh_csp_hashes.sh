#!/usr/bin/env bash
# Synchronize the strict CSP hashes for every inline JSON-LD block emitted by
# ui/index.html and the generated route-specific HTML shells.
#
# Route metadata changes alter the JSON-LD graph, so refreshing only the source
# index hashes is insufficient. This helper delegates to the complete SEO sync:
# TypeScript check, Vite build, static route generation, route/source CSP hash
# synchronization, sitemap + llms snapshots, and SEO verification.
#
# Usage:
#   bash scripts/refresh_csp_hashes.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v npm >/dev/null 2>&1; then
    echo "[refresh_csp_hashes] npm is required" >&2
    exit 1
fi

echo "[refresh_csp_hashes] rebuilding public route shells and synchronizing JSON-LD hashes"
npm --prefix ui run seo:sync
echo "[refresh_csp_hashes] complete"
