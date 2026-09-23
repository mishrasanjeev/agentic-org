#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CI guard: the cross-loop ratchet baseline only ever moves down.

`cross_loop_baseline.txt` is how many times the cross-loop guard
(`core/database.py`) may trip in a test run. A test session counts the trips
and fails above the baseline (`tests/conftest.py`), so the number is the debt
recorded in FINDINGS A-58 — and raising it would silently license a new
violation, which is exactly what the ratchet exists to prevent.

This compares the baseline on this branch with the one on the base ref and
fails when it has grown. Lowering it, or leaving it alone, passes.

    python scripts/check_cross_loop_baseline.py --base origin/main

Fails closed: if either value cannot be read, it exits 2 rather than passing.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASELINE_FILE = "cross_loop_baseline.txt"
REPO_ROOT = Path(__file__).resolve().parents[1]


class BaselineError(RuntimeError):
    """The baseline could not be read on one side of the comparison."""


def parse_baseline(text: str, where: str) -> int:
    """Read the count from the first line, ignoring the `#` commentary."""
    first = text.split("\n", 1)[0]
    value = first.split("#", 1)[0].strip()
    try:
        return int(value)
    except ValueError as exc:
        raise BaselineError(f"{where}: {first!r} does not start with a number") from exc


def baseline_here() -> int:
    path = REPO_ROOT / BASELINE_FILE
    try:
        return parse_baseline(path.read_text(encoding="utf-8"), str(path))
    except OSError as exc:
        raise BaselineError(f"{path} cannot be read: {exc}") from exc


def baseline_at(ref: str) -> int:
    result = subprocess.run(  # noqa: S603
        ["git", "show", f"{ref}:{BASELINE_FILE}"],  # noqa: S607 - git is on PATH in CI
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip()
        if "does not exist" in message or "exists on disk" in message:
            # The base predates the ratchet: any baseline is an improvement.
            return sys.maxsize
        raise BaselineError(f"{ref}:{BASELINE_FILE} cannot be read: {message}")
    return parse_baseline(result.stdout, f"{ref}:{BASELINE_FILE}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="ref to compare against")
    args = parser.parse_args(argv)

    try:
        here, there = baseline_here(), baseline_at(args.base)
    except BaselineError as exc:
        print(f"check_cross_loop_baseline: {exc}", file=sys.stderr)
        return 2

    if here > there:
        print(
            f"check_cross_loop_baseline: the baseline rose from {there} ({args.base}) to "
            f"{here}. It only moves down: a run that trips the cross-loop guard more often "
            "than the baseline has introduced a cross-loop database use. Fix the call "
            "(core.database.run_db_coroutine_sync, or run_async in a worker process) "
            "rather than raising the number. See FINDINGS A-58.",
            file=sys.stderr,
        )
        return 1

    moved = "unchanged" if here == there else f"lowered from {there}"
    print(f"check_cross_loop_baseline: {here} ({moved})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
