#!/usr/bin/env python3
"""CI guard: fail if a PR changes ORM models or init_db() without a migration.

Compares the current ref to a base ref (``origin/main`` by default, override
with ``BASE_REF``). If any file under ``core/models/`` or ``core/database.py``
changed but no new file was added under ``migrations/versions/``, the script
exits non-zero.

Run locally:

    BASE_REF=origin/main python scripts/check_migration_required.py
"""

from __future__ import annotations

import os
import subprocess
import sys

MODEL_PREFIXES = ("core/models/", "core/database.py")
MIGRATION_PREFIX = "migrations/versions/"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()  # noqa: S603, S607


def _changed_files(base: str, head: str) -> list[str]:
    # --diff-filter=d ignores deletions so removing a model doesn't falsely
    # require a new migration.
    try:
        out = _git("diff", "--name-only", "--diff-filter=d", f"{base}...{head}")
    except subprocess.CalledProcessError:
        # fall back to simple diff when merge-base is unavailable
        out = _git("diff", "--name-only", "--diff-filter=d", base, head)
    return [line.strip() for line in out.splitlines() if line.strip()]


def _added_files(base: str, head: str) -> list[str]:
    try:
        out = _git("diff", "--name-only", "--diff-filter=A", f"{base}...{head}")
    except subprocess.CalledProcessError:
        out = _git("diff", "--name-only", "--diff-filter=A", base, head)
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    base = os.getenv("BASE_REF", "origin/main")
    head = os.getenv("HEAD_REF", "HEAD")

    changed = _changed_files(base, head)
    added = _added_files(base, head)

    model_touched = [
        f for f in changed if any(f.startswith(p) or f == p for p in MODEL_PREFIXES)
    ]
    migration_added = [f for f in added if f.startswith(MIGRATION_PREFIX) and f.endswith(".py")]

    if model_touched and not migration_added:
        print("::error::Schema change detected without a matching Alembic migration.", file=sys.stderr)
        print("Files changed that require a migration:", file=sys.stderr)
        for f in model_touched:
            print(f"  - {f}", file=sys.stderr)
        print(
            "\nAdd a new file under migrations/versions/ (alembic revision -m '<title>'), "
            "update down_revision to the current head, and include the DDL.",
            file=sys.stderr,
        )
        return 1

    if model_touched:
        print(
            f"OK: {len(model_touched)} model/schema file(s) changed, "
            f"{len(migration_added)} migration file(s) added."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
