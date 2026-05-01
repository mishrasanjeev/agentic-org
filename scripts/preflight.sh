#!/usr/bin/env bash
# preflight — run every check CI will run, locally, before git push.
#
# Usage:
#   bash scripts/preflight.sh            # full gate
#   bash scripts/preflight.sh --fast     # skip heavy checks (ui build, playwright)
#   SKIP_UI=1 bash scripts/preflight.sh  # python-only
#   SKIP_BANDIT=1 ...                    # skip security scan
#
# The goal is to catch before push every class of CI failure we've hit:
#   - ruff (whole tree, not just touched files)
#   - bandit on api/auth/core
#   - alembic revision-id length (varchar(32) cap)
#   - accidental `verify=False` / `# noqa: S501` that ruff misses
#   - targeted pytest (regression + fast unit), no-cov so locked coverage
#     files don't make us guess whether a real test broke
#   - ui: tsc --noEmit + build
#
# Exits non-zero on the first failing gate. Print a short summary at the
# end so the reviewer sees what ran.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FAST=${FAST:-0}
SKIP_UI=${SKIP_UI:-0}
SKIP_BANDIT=${SKIP_BANDIT:-0}
SKIP_PYTEST=${SKIP_PYTEST:-0}
SKIP_MYPY=${SKIP_MYPY:-0}
SKIP_MODULE_COV=${SKIP_MODULE_COV:-0}
SKIP_CONSISTENCY=${SKIP_CONSISTENCY:-0}
SKIP_TAG_CHECK=${SKIP_TAG_CHECK:-0}

if [[ "${1:-}" == "--fast" ]]; then
  FAST=1
fi

RED='\033[0;31m'
GRN='\033[0;32m'
YEL='\033[0;33m'
BLU='\033[0;34m'
NC='\033[0m'

PASSED=()
FAILED=()

run_step() {
  local name="$1"
  shift
  echo -e "${BLU}[preflight] → ${name}${NC}"
  if "$@"; then
    PASSED+=("$name")
    echo -e "${GRN}[preflight] ✓ ${name}${NC}"
  else
    FAILED+=("$name")
    echo -e "${RED}[preflight] ✗ ${name}${NC}"
    summary
    exit 1
  fi
}

summary() {
  echo ""
  echo -e "${BLU}[preflight] summary${NC}"
  for s in "${PASSED[@]}"; do echo -e "  ${GRN}pass${NC}  $s"; done
  for s in "${FAILED[@]}"; do echo -e "  ${RED}FAIL${NC}  $s"; done
  echo ""
}

# ---------------------------------------------------------------------------
# 1. Branch safety — never let a push to main slip through.
# ---------------------------------------------------------------------------
branch_check() {
  local branch
  branch=$(git rev-parse --abbrev-ref HEAD)
  if [[ "$branch" == "main" || "$branch" == "master" ]]; then
    echo -e "${RED}[preflight] refuse: you are on '$branch'. Create a feature branch and retry.${NC}"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# 2. Ruff — whole tree. Touched-files-only misses import-sort issues in
#    sibling files.
# ---------------------------------------------------------------------------
ruff_check() {
  python -m ruff check .
}

# ---------------------------------------------------------------------------
# 2b. Mypy — same invocation as the CI lint job (.github/workflows/deploy.yml).
# Without this here, type errors fail CI on the FIRST gate even though
# every local preflight passed. PR #399 (2026-04-30) was caught by this
# exact gap — local green, CI red on a missing constructor argument.
# ---------------------------------------------------------------------------
mypy_check() {
  if [[ "$SKIP_MYPY" == "1" ]]; then
    echo "[preflight] skipped (SKIP_MYPY=1)"
    return 0
  fi
  # ``codex-pytest-basetemp/`` is pytest's per-run scratch directory
  # left behind by previous local runs. CI starts from a fresh checkout
  # so it never sees those files; locally they trigger spurious
  # "Duplicate module" mypy errors. Exclude it explicitly so the local
  # gate matches CI's effective scope.
  python -m mypy --ignore-missing-imports \
    --exclude '(codex-pytest-basetemp|codex-pytest-temp)' \
    .
}

# ---------------------------------------------------------------------------
# 3. Bandit — high severity anywhere under api/auth/core.
# ---------------------------------------------------------------------------
bandit_check() {
  if [[ "$SKIP_BANDIT" == "1" ]]; then
    echo "[preflight] skipped (SKIP_BANDIT=1)"
    return 0
  fi
  # Only flag actual errors (HIGH severity, HIGH confidence).  Warnings
  # about #nosec-without-test are noise.
  python -m bandit -r api auth core -x migrations,tests -ll -iii -q
}

# ---------------------------------------------------------------------------
# 4. Alembic revision IDs — alembic_version.version_num is VARCHAR(32).
# ---------------------------------------------------------------------------
alembic_id_check() {
  local bad=0
  local rev
  for f in migrations/versions/*.py; do
    rev=$(python -c "
import re, sys, pathlib
src = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
m = re.search(r'^revision\s*=\s*[\"\\'](.+?)[\"\\']', src, re.M)
if m: print(m.group(1))
" "$f")
    if [[ -n "$rev" ]] && (( ${#rev} > 32 )); then
      echo -e "${RED}[preflight] alembic revision too long (${#rev} > 32): $rev in $f${NC}"
      bad=1
    fi
  done
  return $bad
}

# ---------------------------------------------------------------------------
# 5. verify=False scan — production code must never disable TLS. Tests
#    and scripts are exempt.
# ---------------------------------------------------------------------------
verify_false_scan() {
  local hits
  hits=$(git grep -nE 'verify\s*=\s*False' -- 'api/**' 'core/**' 'auth/**' 'connectors/**' || true)
  if [[ -n "$hits" ]]; then
    echo -e "${RED}[preflight] verify=False in production code:${NC}"
    echo "$hits"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# 6. Pytest — regression suite + targeted unit tests. Fast: ~30s.
# ---------------------------------------------------------------------------
pytest_check() {
  if [[ "$SKIP_PYTEST" == "1" ]]; then
    echo "[preflight] skipped (SKIP_PYTEST=1)"
    return 0
  fi
  # regression/ is cheap and exhaustive; unit/ has the model-key-set guards
  # that trip us when serializers change. Runs with the addopts coverage
  # config so the following module-coverage check has .coverage to read;
  # set SKIP_MODULE_COV=1 to opt back into the old --no-cov fast path.
  if [[ "$SKIP_MODULE_COV" == "1" ]]; then
    python -m pytest tests/regression/ tests/unit/ -q --no-cov
  else
    python -m pytest tests/regression/ tests/unit/ -q
  fi
}

# ---------------------------------------------------------------------------
# 7. UI — type check + build. Skip with SKIP_UI=1 when working on backend only.
# ---------------------------------------------------------------------------
ui_check() {
  if [[ "$SKIP_UI" == "1" || ! -d "$REPO_ROOT/ui" ]]; then
    echo "[preflight] skipped (SKIP_UI=1 or no ui/)"
    return 0
  fi
  (cd ui && npx tsc --noEmit)
}

ui_build() {
  if [[ "$SKIP_UI" == "1" || "$FAST" == "1" || ! -d "$REPO_ROOT/ui" ]]; then
    echo "[preflight] skipped (SKIP_UI=1 or --fast)"
    return 0
  fi
  (cd ui && npm run build --silent)
}

# ---------------------------------------------------------------------------
# 8. Cross-surface consistency sweep — version agreement, runtime
# registry counts, no stale public claims, MCP vs LangGraph tool index.
# ---------------------------------------------------------------------------
consistency_sweep() {
  if [[ "$SKIP_CONSISTENCY" == "1" ]]; then
    echo "[preflight] skipped (SKIP_CONSISTENCY=1)"
    return 0
  fi
  python scripts/consistency_sweep.py
}

# ---------------------------------------------------------------------------
# 9. Per-module coverage floor — auth/*, api/v1/{auth,governance,mcp}.py,
# core/database.py. Global --cov-fail-under handled by pytest in the
# unit-tests CI step. Requires pytest_check to have run so .coverage
# exists; skipped when pytest was skipped.
# ---------------------------------------------------------------------------
module_coverage_check() {
  if [[ "$SKIP_PYTEST" == "1" || "$SKIP_MODULE_COV" == "1" ]]; then
    echo "[preflight] skipped (SKIP_PYTEST=1 or SKIP_MODULE_COV=1)"
    return 0
  fi
  python scripts/check_module_coverage.py
}

# ---------------------------------------------------------------------------
# 10. Critical-path Playwright tags — every tag must appear in ≥1 spec
# so a deleted/renamed describe can't silently drop coverage.
# ---------------------------------------------------------------------------
critical_tag_check() {
  if [[ "$SKIP_TAG_CHECK" == "1" || ! -d "$REPO_ROOT/ui/e2e" ]]; then
    echo "[preflight] skipped (SKIP_TAG_CHECK=1 or no ui/e2e/)"
    return 0
  fi
  python scripts/check_critical_path_tags.py
}

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
run_step "branch safety"          branch_check
run_step "ruff (whole tree)"      ruff_check
run_step "mypy (whole tree)"      mypy_check
run_step "bandit (api/auth/core)" bandit_check
run_step "alembic revision <=32"  alembic_id_check
run_step "verify=False scan"      verify_false_scan
run_step "pytest regression+unit" pytest_check
run_step "ui tsc"                 ui_check
run_step "ui build"               ui_build
run_step "consistency sweep"      consistency_sweep
run_step "module coverage floor"  module_coverage_check
run_step "critical-path tags"     critical_tag_check

summary
echo -e "${GRN}[preflight] all gates passed. Safe to push.${NC}"
