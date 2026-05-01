#!/usr/bin/env bash
# SEC-010: regenerate digest pins for production base images.
#
# Run when Renovate / Dependabot bumps a base image AND CI image scan
# (Trivy/Grype) confirms upstream is safe. Edits Dockerfile,
# Dockerfile.ui, Dockerfile.ui.cloudrun in place — diff before commit.
#
# Usage:
#   bash scripts/refresh_image_digests.sh
#
# Requires: curl, python3 (for JSON parsing). No docker daemon needed.
set -euo pipefail

cd "$(dirname "$0")/.."

resolve_digest() {
    # Args: <library/image> <tag>
    # Prints the linux/amd64 image digest from Docker Hub's public API.
    local repo="$1" tag="$2"
    curl -fsSL "https://hub.docker.com/v2/repositories/${repo}/tags/${tag}/" \
      | python -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('digest', ''))
"
}

declare -A IMAGES=(
    ["python:3.14-slim"]="library/python|3.14-slim|Dockerfile"
    ["node:25-slim"]="library/node|25-slim|Dockerfile.ui Dockerfile.ui.cloudrun"
    ["nginx:alpine"]="library/nginx|alpine|Dockerfile.ui Dockerfile.ui.cloudrun"
)

echo "[refresh_image_digests] Resolving current digests from Docker Hub..."
date -u +"=== %Y-%m-%dT%H:%M:%SZ ==="

for label in "${!IMAGES[@]}"; do
    IFS='|' read -r repo tag files <<< "${IMAGES[$label]}"
    digest=$(resolve_digest "$repo" "$tag")
    if [[ -z "$digest" ]]; then
        echo "[refresh_image_digests] FAILED to resolve $label" >&2
        exit 1
    fi
    printf "  %-25s -> %s\n" "$label" "$digest"
    for f in $files; do
        if [[ ! -f "$f" ]]; then
            echo "[refresh_image_digests] missing file $f" >&2
            continue
        fi
        # Replace any existing pin (with or without digest) to the new digest.
        # Pattern: FROM ${tag}                       -> tag-only
        #          FROM ${tag}@sha256:...             -> already pinned
        sed -i -E "s|^(FROM ${repo#library/}:${tag})(@sha256:[a-f0-9]+)?(.*)$|\1@${digest}\3|" "$f"
    done
done

echo "[refresh_image_digests] Done. Diff:"
git diff --stat -- Dockerfile Dockerfile.ui Dockerfile.ui.cloudrun
