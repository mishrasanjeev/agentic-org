#!/usr/bin/env python3
"""Per-module coverage-floor enforcement.

pytest-cov's `--cov-fail-under` is global-only. This script shells
out to `coverage report --include=<glob>` and enforces per-module
floors for modules the Enterprise Readiness plan marks as critical.

Usage (run after pytest produced .coverage):

    python -m pytest tests/regression/ tests/unit/
    python scripts/check_module_coverage.py

Exits 0 if every configured module meets its floor, non-zero on the
first miss.

Modules not in the pytest `--cov=` set (e.g. connectors, migrations)
are skipped — adding them here without also instrumenting them would
report a noisy 0%.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Floors pinned to the measured baseline minus a safety margin
# (regression guard, not aspirational). The 85% Enterprise Readiness
# target is surfaced as a warning — raise these floors when the real
# suite catches up. Only include modules actually instrumented by
# pytest-cov (--cov=core --cov=api --cov=auth --cov=workflows --cov=scaling).
# Baseline measured 2026-04-19 on regression/ + unit/ suite.
MODULE_FLOORS: dict[str, float] = {
    "auth/*": 70.0,           # 74% baseline
    "api/v1/auth.py": 50.0,   # 52% baseline
    "api/v1/governance.py": 35.0,  # 40% baseline
    "api/v1/mcp.py": 45.0,    # 49% baseline
    "core/database.py": 30.0,  # 32% baseline
}

# Warn threshold — shows up in the output but doesn't fail the build.
CRITICAL_TARGET = 85.0


def _module_percent(include_glob: str) -> float | None:
    """Return the TOTAL line-coverage percent for paths matching glob."""
    proc = subprocess.run(  # noqa: S603 — trusted inputs, literal argv
        [sys.executable, "-m", "coverage", "report", f"--include={include_glob}"],
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
        if "No data" in line or "no data" in line.lower():
            return None
    return None


def main() -> int:
    if not (ROOT / ".coverage").exists() and not (ROOT / "coverage.xml").exists():
        print(
            "[coverage] no .coverage or coverage.xml — run pytest first",
            file=sys.stderr,
        )
        return 2

    print("=" * 60)
    print("Per-module coverage floor check")
    print("=" * 60)
    fails: list[tuple[str, float, float]] = []
    warns: list[tuple[str, float]] = []
    for glob, floor in MODULE_FLOORS.items():
        pct = _module_percent(glob)
        if pct is None:
            print(f"[SKIP] {glob:<40}   n/a  (not instrumented / no files)")
            continue
        mark = "OK  " if pct >= floor else "FAIL"
        warn_suffix = ""
        if pct < CRITICAL_TARGET:
            warn_suffix = f" (target: {CRITICAL_TARGET:.0f}%)"
            warns.append((glob, pct))
        print(f"[{mark}] {glob:<40} {pct:>6.2f}% (floor {floor:.0f}%){warn_suffix}")
        if pct < floor:
            fails.append((glob, pct, floor))

    print("=" * 60)
    if fails:
        print(f"{len(fails)} module(s) below floor:")
        for glob, pct, floor in fails:
            print(f"  - {glob}: {pct:.2f}% < {floor:.0f}%")
        return 1
    if warns:
        print(
            f"{len(warns)} module(s) below aspirational target "
            f"{CRITICAL_TARGET:.0f}% (warn only):"
        )
        for glob, pct in warns:
            print(f"  - {glob}: {pct:.2f}%")
    print("all module floors met")
    return 0


if __name__ == "__main__":
    sys.exit(main())
