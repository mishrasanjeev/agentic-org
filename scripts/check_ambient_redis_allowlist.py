#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CI guard: the ambient-Redis allowlist only ever shrinks.

`tests/ambient_redis_allowlist.txt` names the test files that still reach a
Redis when the machine has one (FINDINGS A-59). The connection is refused
either way, so a run stays hermetic; the list is there so a *new* file fails
instead of joining them quietly, which only works while the list does not grow.

This compares the list on this branch with the one on the base ref and fails
when an entry was added. Removing entries, or leaving the list alone, passes.

    python scripts/check_ambient_redis_allowlist.py --base origin/main

Fails closed: if either side cannot be read, it exits 2 rather than passing.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ALLOWLIST_FILE = "tests/ambient_redis_allowlist.txt"
REPO_ROOT = Path(__file__).resolve().parents[1]


class AllowlistError(RuntimeError):
    """The allowlist could not be read on one side of the comparison."""


def parse_allowlist(text: str) -> frozenset[str]:
    """Entries, ignoring blank lines and `#` commentary."""
    return frozenset(
        entry
        for line in text.splitlines()
        if (entry := line.split("#", 1)[0].strip())
    )


def allowlist_here() -> frozenset[str]:
    path = REPO_ROOT / ALLOWLIST_FILE
    try:
        return parse_allowlist(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AllowlistError(f"{path} cannot be read: {exc}") from exc


def allowlist_at(ref: str) -> frozenset[str] | None:
    """The base ref's entries, or ``None`` when it predates the file."""
    result = subprocess.run(  # noqa: S603
        ["git", "show", f"{ref}:{ALLOWLIST_FILE}"],  # noqa: S607 - git is on PATH in CI
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip()
        if "does not exist" in message or "exists on disk" in message:
            return None
        raise AllowlistError(f"{ref}:{ALLOWLIST_FILE} cannot be read: {message}")
    return parse_allowlist(result.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="ref to compare against")
    args = parser.parse_args(argv)

    try:
        here = allowlist_here()
        there = allowlist_at(args.base)
    except AllowlistError as exc:
        print(f"check_ambient_redis_allowlist: {exc}", file=sys.stderr)
        return 2

    if there is None:
        print(f"check_ambient_redis_allowlist: {len(here)} entries ({args.base} has no list yet)")
        return 0

    added = sorted(here - there)
    if added:
        print(
            "check_ambient_redis_allowlist: these files were added to the allowlist:\n  "
            + "\n  ".join(added)
            + "\n\nThe list only shrinks. A test that reaches Redis lets the machine decide "
            "its result and leaves state for the next run: give the code under test an "
            "explicit client or a fake, move the test to tests/integration/, or mark it "
            "ambient_redis if it is about the lazy client itself. See FINDINGS A-59.",
            file=sys.stderr,
        )
        return 1

    removed = len(there - here)
    moved = f"{removed} removed" if removed else "unchanged"
    print(f"check_ambient_redis_allowlist: {len(here)} entries ({moved})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
