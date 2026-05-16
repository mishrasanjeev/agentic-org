#!/usr/bin/env python3
"""CI guard: fail if a PR changes schema-bearing files without a migration.

Compares the current ref to a base ref (``origin/main`` by default, override
with ``BASE_REF``). If any ORM model file changed, or ``core/database.py``
adds startup-time DDL, but no new file was added under
``migrations/versions/``, the script exits non-zero. It also rejects multiple
Alembic heads unless this is an explicit merge-head PR.

Run locally:

    BASE_REF=origin/main python scripts/check_migration_required.py
"""

from __future__ import annotations

import os
import subprocess
import sys

MIGRATION_PREFIX = "migrations/versions/"
CORE_DATABASE_FILE = "core/database.py"
ALLOW_MULTIPLE_ALEMBIC_HEADS_ENV = "AGENTICORG_ALLOW_MULTIPLE_ALEMBIC_HEADS_FOR_MERGE_PR"

SCHEMA_DDL_PATTERNS = (
    "ALTER TABLE",
    "CREATE INDEX",
    "CREATE OR REPLACE FUNCTION",
    "CREATE POLICY",
    "CREATE TABLE",
    "CREATE TRIGGER",
    "DROP POLICY",
    "DROP TRIGGER",
    "ENABLE ROW LEVEL SECURITY",
)

# Files under schema-relevant paths that never carry schema DDL. Re-export-only
# packages can be touched without a migration — adding them here keeps
# the guard honest instead of forcing meaningless empty migrations.
IGNORED_MODEL_FILES = frozenset({
    "core/models/__init__.py",
})


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


def _diff(base: str, head: str, path: str) -> str:
    try:
        return _git("diff", "-U0", f"{base}...{head}", "--", path)
    except subprocess.CalledProcessError:
        return _git("diff", "-U0", base, head, "--", path)


def _core_database_added_schema_ddl(base: str, head: str) -> bool:
    added_lines = [
        line[1:].strip().upper()
        for line in _diff(base, head, CORE_DATABASE_FILE).splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    return any(pattern in line for line in added_lines for pattern in SCHEMA_DDL_PATTERNS)


def _schema_relevant_changed_files(base: str, head: str, changed: list[str]) -> list[str]:
    relevant = []
    for path in changed:
        if path in IGNORED_MODEL_FILES:
            continue
        if path == CORE_DATABASE_FILE:
            if _core_database_added_schema_ddl(base, head):
                relevant.append(path)
            continue
        if path.startswith("core/models/"):
            relevant.append(path)
    return relevant


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _alembic_heads() -> list[str]:
    out = subprocess.check_output(  # noqa: S603
        [sys.executable, "-m", "alembic", "heads"],
        stderr=subprocess.STDOUT,
        text=True,
    )
    return [line.split()[0] for line in out.splitlines() if line.strip()]


def _multiple_heads_allowed() -> bool:
    return _truthy_env(ALLOW_MULTIPLE_ALEMBIC_HEADS_ENV)


def main() -> int:
    base = os.getenv("BASE_REF", "origin/main")
    head = os.getenv("HEAD_REF", "HEAD")

    alembic_heads = _alembic_heads()
    if len(alembic_heads) > 1 and not _multiple_heads_allowed():
        print("::error::Multiple Alembic heads detected without an explicit merge-head plan.", file=sys.stderr)
        for revision in alembic_heads:
            print(f"  - {revision}", file=sys.stderr)
        print(
            "\nAdd an Alembic merge revision so production has one head, or set "
            f"{ALLOW_MULTIPLE_ALEMBIC_HEADS_ENV}=1 only on an explicit merge-head PR.",
            file=sys.stderr,
        )
        return 1

    changed = _changed_files(base, head)
    added = _added_files(base, head)

    model_touched = _schema_relevant_changed_files(base, head, changed)
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
