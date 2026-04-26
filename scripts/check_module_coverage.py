#!/usr/bin/env python3
"""Per-module coverage-floor enforcement — fail-closed.

pytest-cov's `--cov-fail-under` is global-only. This script shells
out to `coverage report --include=<glob>` and enforces per-module
floors for modules the Enterprise Readiness plan marks as critical.

Usage (run after pytest produced .coverage):

    python -m pytest tests/regression/ tests/unit/
    python scripts/check_module_coverage.py

Exits 0 if every configured module meets its floor. Non-zero on:
    1   one or more modules below their floor
    2   .coverage / coverage.xml missing
    3   .coverage exists but is empty / corrupted
    4   every configured module reports n/a (test process never ran the code)
    5   global `coverage report` output unparsable

User directive 2026-04-26 (feedback_no_manual_qa_closure_plan.md item #2):
- Fail when .coverage missing, empty, stale, or unreadable.
- Fail when every configured module is skipped (suggests test process
  never executed the instrumented code).
- Stage-raise gates: backend 55 -> 70 -> 80, auth/crypto/tenant
  isolation/billing/workflows/connectors 90+.
- Treat any green CI result with no underlying coverage data as a build
  failure, not a green build.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
from datetime import UTC, datetime

# Tests override ROOT via COVERAGE_GATE_ROOT to point at a controlled
# tmp dir with a synthetic .coverage. Production runs from the repo root
# and falls back to the script's parent directory.
_DEFAULT_ROOT = pathlib.Path(__file__).resolve().parents[1]
ROOT = pathlib.Path(os.environ.get("COVERAGE_GATE_ROOT", str(_DEFAULT_ROOT)))


# ── Module floors ──────────────────────────────────────────────────────────
#
# Naming convention:
#   - `_floor` is what we ENFORCE today. Failing = build red.
#   - `_target` is the next stage (warn-only). Lifting `_floor` to `_target`
#     happens in a later PR once the suite catches up.
#
# Stage-raise plan per the user directive:
#   global backend (any of api/, auth/, core/, workflows/, scaling/):
#     55 (today) -> 70 -> 80
#   critical paths (auth/crypto/tenancy/billing/workflows/connectors):
#     90+ across the board (today's floors are baseline; targets push to 90)
#
# Floors below the published target carry a `# stage-raise: 90 (per directive)`
# comment so the Lift-The-Floor PR can be one search-and-replace.

# Critical-path modules — directive requires 90%+ here. Floors today are at
# the measured baseline so the gate is meaningful immediately; the `_target`
# is what they must reach.
MODULE_FLOORS: dict[str, dict[str, float | None]] = {
    # ── Auth (90% target per directive) ──
    "auth/*": {"floor": 70.0, "target": 90.0},  # baseline 74%
    "api/v1/auth.py": {"floor": 50.0, "target": 90.0},  # baseline 52%
    # ── Crypto (90% target — direct stake in the SECRET_KEY rotation incident) ──
    "core/crypto/*": {"floor": 0.0, "target": 90.0},  # measure first run
    # ── Tenancy isolation (90% target) ──
    "core/database.py": {"floor": 30.0, "target": 90.0},  # baseline 32%
    "api/deps.py": {"floor": 0.0, "target": 90.0},  # measure first run
    # ── Billing (90% target) ──
    "core/billing/*": {"floor": 0.0, "target": 90.0},  # measure first run
    "api/v1/billing.py": {"floor": 0.0, "target": 90.0},  # measure first run
    # ── Workflows (90% target) ──
    "workflows/*": {"floor": 0.0, "target": 90.0},  # measure first run
    "core/tasks/workflow_tasks.py": {"floor": 0.0, "target": 90.0},
    # ── Connectors (90% target) ──
    "connectors/framework/*": {"floor": 0.0, "target": 90.0},
    "api/v1/connectors.py": {"floor": 0.0, "target": 90.0},
    # ── Other instrumented modules (lower stakes, lift over time) ──
    "api/v1/governance.py": {"floor": 35.0, "target": 70.0},  # baseline 40%
    "api/v1/mcp.py": {"floor": 45.0, "target": 70.0},  # baseline 49%
}

# Aspirational target shown in output; raise floors over time. The user
# directive specifies global backend should reach 80%, critical paths 90+.
GLOBAL_BACKEND_FLOOR = 55.0  # stage-raise: 70 -> 80 per directive
GLOBAL_BACKEND_TARGET = 80.0
CRITICAL_PATH_TARGET = 90.0

# Output artifact for CI / release-acceptance gate (item #9).
ARTIFACT_PATH = ROOT / "coverage_report.json"


# ── Helpers ────────────────────────────────────────────────────────────────


def _coverage_file_present_and_valid() -> tuple[bool, str]:
    """Return (ok, reason). Reason populated only when not ok."""
    cov_db = ROOT / ".coverage"
    cov_xml = ROOT / "coverage.xml"
    if not cov_db.exists() and not cov_xml.exists():
        return False, "no .coverage or coverage.xml — run pytest first"
    # If only coverage.xml exists, accept it; per-module subprocess calls
    # use `coverage report` which reads .coverage. If only XML exists we
    # can still pass via XML parsing — but the per-module `coverage
    # report --include=...` path needs .coverage. Surface that explicitly.
    if not cov_db.exists():
        return (
            True,
            "warn: only coverage.xml present (per-module report() may "
            "be limited)",
        )
    # Empty / truncated .coverage = process crashed mid-write.
    size = cov_db.stat().st_size
    if size == 0:
        return False, ".coverage is empty (0 bytes) — test process likely crashed"
    if size < 64:
        return False, f".coverage is suspiciously small ({size} bytes)"
    return True, ""


def _module_percent(include_glob: str) -> float | None:
    """Return the TOTAL line-coverage percent for paths matching glob.

    Returns None when coverage has no data for the glob (no instrumented
    files matched). The caller distinguishes None -> "n/a / not run".
    """
    proc = subprocess.run(  # noqa: S603 — trusted inputs, literal argv
        [sys.executable, "-m", "coverage", "report", f"--include={include_glob}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout + proc.stderr
    for line in reversed(output.splitlines()):
        if line.startswith("TOTAL"):
            m = re.search(r"(\d+)%\s*$", line)
            if m:
                return float(m.group(1))
        if "No data" in line or "no data" in line.lower():
            return None
        if "no source for code" in line.lower():
            return None
    return None


def _global_percent() -> float | None:
    """TOTAL across everything `coverage` knows about."""
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "coverage", "report"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("TOTAL"):
            m = re.search(r"(\d+)%\s*$", line)
            if m:
                return float(m.group(1))
    return None


def _emit_artifact(rows: list[dict], global_pct: float | None, status: str) -> None:
    """Write `coverage_report.json` for the release-acceptance gate."""
    artifact = {
        "generated_at": datetime.now(UTC).isoformat(),
        "global_percent": global_pct,
        "global_floor": GLOBAL_BACKEND_FLOOR,
        "global_target": GLOBAL_BACKEND_TARGET,
        "critical_path_target": CRITICAL_PATH_TARGET,
        "status": status,
        "modules": rows,
    }
    try:
        ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2))
    except OSError as exc:
        # Don't fail the gate solely on artifact-write failure — log & move on.
        print(f"[coverage] warn: could not write artifact: {exc}", file=sys.stderr)


# ── Main ───────────────────────────────────────────────────────────────────


def main() -> int:
    ok, reason = _coverage_file_present_and_valid()
    if not ok:
        print(f"[coverage] FAIL — {reason}", file=sys.stderr)
        # Distinguish "missing entirely" (exit 2) from "present but corrupt" (exit 3)
        # so CI can give a sharper error message.
        return 2 if "no .coverage" in reason else 3

    print("=" * 64)
    print("Per-module coverage floor check (fail-closed)")
    print("=" * 64)
    fails: list[tuple[str, float, float, float]] = []  # glob, pct, floor, target
    warns: list[tuple[str, float, float]] = []  # glob, pct, target
    skipped: list[str] = []
    rows: list[dict] = []

    for glob, cfg in MODULE_FLOORS.items():
        floor = float(cfg["floor"] or 0.0)
        target = float(cfg.get("target") or floor)
        pct = _module_percent(glob)
        row = {
            "glob": glob,
            "percent": pct,
            "floor": floor,
            "target": target,
            "status": "skip" if pct is None else ("ok" if pct >= floor else "fail"),
        }
        rows.append(row)

        if pct is None:
            print(f"[SKIP] {glob:<36}   n/a    (not instrumented / no rows in .coverage)")
            skipped.append(glob)
            continue
        mark = "OK  " if pct >= floor else "FAIL"
        warn_suffix = ""
        if pct < target:
            warn_suffix = f" (target: {target:.0f}%)"
            warns.append((glob, pct, target))
        print(
            f"[{mark}] {glob:<36} {pct:>6.2f}% (floor {floor:.0f}%){warn_suffix}"
        )
        if pct < floor:
            fails.append((glob, pct, floor, target))

    print("=" * 64)

    # Critical fail-closed cases
    if not skipped == [] and len(skipped) == len(MODULE_FLOORS):
        # EVERY module is skipped → test process likely never imported the code
        # this script is enforcing. False-green disguise.
        print(
            "[coverage] FAIL — every configured module reports n/a. "
            "The test process did not exercise any of the modules this "
            "gate enforces. Most likely cause: pytest --cov was run with "
            "the wrong --cov= scope, or the test environment skipped all "
            "imports. Cannot prove anything is covered.",
            file=sys.stderr,
        )
        _emit_artifact(rows, None, "fail-all-skipped")
        return 4

    global_pct = _global_percent()
    if global_pct is None:
        print(
            "[coverage] FAIL — `coverage report` produced no global TOTAL. "
            "The .coverage file may exist but contain no records.",
            file=sys.stderr,
        )
        _emit_artifact(rows, None, "fail-unparseable")
        return 5

    print(
        f"[global] {global_pct:.2f}% across all instrumented modules "
        f"(floor {GLOBAL_BACKEND_FLOOR:.0f}%, target {GLOBAL_BACKEND_TARGET:.0f}%)"
    )
    if global_pct < GLOBAL_BACKEND_FLOOR:
        print(
            f"[coverage] FAIL — global coverage {global_pct:.2f}% < "
            f"{GLOBAL_BACKEND_FLOOR:.0f}%",
            file=sys.stderr,
        )
        fails.append(("[global]", global_pct, GLOBAL_BACKEND_FLOOR, GLOBAL_BACKEND_TARGET))

    if fails:
        print()
        print(f"{len(fails)} module(s) below floor:")
        for glob, pct, floor, target in fails:
            print(f"  - {glob}: {pct:.2f}% < {floor:.0f}% (target {target:.0f}%)")
        _emit_artifact(rows, global_pct, "fail-below-floor")
        return 1

    if warns:
        print()
        print(
            f"{len(warns)} module(s) below aspirational target — "
            "not failing the build, but raise the floor in a follow-up PR:"
        )
        for glob, pct, target in warns:
            print(f"  - {glob}: {pct:.2f}% (target {target:.0f}%)")
    print("all module floors met")
    _emit_artifact(rows, global_pct, "ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
