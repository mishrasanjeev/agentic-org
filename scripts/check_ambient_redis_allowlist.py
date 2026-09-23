#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CI guard: the ambient-Redis allowlist only ever shrinks.

`tests/ambient_redis_allowlist.txt` names the test files that still reach a
Redis when the machine has one (FINDINGS A-59). The connection is refused
either way, so a run stays hermetic; the list is there so a *new* file fails
instead of joining them quietly, which only works while the list does not grow.

Pull requests compare against the **merge base**, not the tip of the base
branch. Comparing a PR with the tip accuses it as soon as another PR removes
an entry. Push events may pass ``--exact-base`` with the before-SHA so a
non-fast-forward update cannot hide growth behind an older common ancestor.

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


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 - git is on PATH in CI and in the tools image
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def parse_allowlist(text: str) -> frozenset[str]:
    """Entries, ignoring blank lines and `#` commentary."""
    return frozenset(
        entry for line in text.splitlines() if (entry := line.split("#", 1)[0].strip())
    )


def allowlist_here() -> frozenset[str]:
    path = REPO_ROOT / ALLOWLIST_FILE
    try:
        return parse_allowlist(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AllowlistError(f"{path} cannot be read: {exc}") from exc


def merge_base(ref: str) -> str:
    """Where this branch diverged, so another branch's removal is not read as ours.

    An empty ref is refused rather than passed to git: ``git show ":path"``
    reads the index, so an empty ``--base`` would compare the branch with
    itself and pass vacuously.
    """
    if not ref.strip():
        raise AllowlistError("--base is empty; pass a ref such as origin/main")
    if _git("cat-file", "-e", f"{ref}^{{commit}}").returncode != 0:
        raise AllowlistError(f"{ref} does not name a commit")
    found = _git("merge-base", ref, "HEAD")
    if found.returncode != 0 or not found.stdout.strip():
        raise AllowlistError(f"no merge base between {ref} and HEAD")
    return found.stdout.strip()


def exact_commit(ref: str) -> str:
    """Validate and use an exact commit rather than finding its merge base."""
    if not ref.strip():
        raise AllowlistError("--base is empty; pass the exact pre-change commit")
    found = _git("rev-parse", "--verify", f"{ref}^{{commit}}")
    if found.returncode != 0 or not found.stdout.strip():
        raise AllowlistError(f"{ref} does not name a commit")
    return found.stdout.strip()


def allowlist_at(ref: str) -> frozenset[str] | None:
    """The entries at ``ref``, or ``None`` when the file does not exist there.

    Decisions come from exit codes, never from git's prose, which is localised
    and changes between versions.
    """
    listed = _git("ls-tree", "--name-only", ref, "--", ALLOWLIST_FILE)
    if listed.returncode != 0:
        raise AllowlistError(f"{ref} cannot be read: git ls-tree exited {listed.returncode}")
    if not listed.stdout.strip():
        return None
    shown = _git("show", f"{ref}:{ALLOWLIST_FILE}")
    if shown.returncode != 0:
        raise AllowlistError(f"{ref}:{ALLOWLIST_FILE} cannot be read")
    return parse_allowlist(shown.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="ref to compare against")
    parser.add_argument(
        "--exact-base",
        action="store_true",
        help="compare against this exact commit (for push-event before SHAs)",
    )
    args = parser.parse_args(argv)

    try:
        here = allowlist_here()
        base = exact_commit(args.base) if args.exact_base else merge_base(args.base)
        there = allowlist_at(base)
    except AllowlistError as exc:
        print(f"check_ambient_redis_allowlist: {exc}", file=sys.stderr)
        return 2

    if there is None:
        print(
            f"check_ambient_redis_allowlist: {len(here)} entries "
            f"(the merge base {base[:8]} has no list yet)"
        )
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
    print(f"check_ambient_redis_allowlist: {len(here)} entries ({moved} since {base[:8]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
