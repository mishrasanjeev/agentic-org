#!/usr/bin/env bash
# install_hooks — point git at the repo-tracked hooks under .githooks/.
# Run once per clone. Idempotent.
#
# After this runs:
#   .git/hooks/*       ignored
#   .githooks/pre-commit  active
#   .githooks/pre-push    active
#
# The AgenticOrg repo uses these to block direct-to-main commits and to
# run the preflight gate before every push.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

HOOKS_DIR=".githooks"

if [[ ! -d "$HOOKS_DIR" ]]; then
  echo "[install_hooks] missing $HOOKS_DIR — aborting"
  exit 1
fi

chmod +x "$HOOKS_DIR"/* 2>/dev/null || true

git config core.hooksPath "$HOOKS_DIR"

echo "[install_hooks] core.hooksPath -> $HOOKS_DIR"
echo "[install_hooks] active hooks:"
ls -1 "$HOOKS_DIR"
echo ""
echo "[install_hooks] done. 'git commit' and 'git push' now run local gates."
