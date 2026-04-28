#!/usr/bin/env python3
"""CI guard: any new alembic migration touching ``*_encrypted`` columns
must import ``core.crypto.migration_helpers``.

Foundation #5 step 3. Pairs with ``core/crypto/migration_helpers.py``
and ``docs/encrypted_column_migration_template.md``.

Compares the current ref to a base ref (``origin/main`` by default,
override with ``BASE_REF``). For each NEWLY ADDED file under
``migrations/versions/`` whose body mentions an ``*_encrypted``
column, the file MUST also import the helpers module. Otherwise the
migration is bypassing the dry-run + decrypt-verify + resumability
+ audit gates the user mandated after the SECRET_KEY incident.

Run locally::

    BASE_REF=origin/main python scripts/check_encrypted_migration_uses_helpers.py

Bypass marker (one line per migration that genuinely doesn't need
the helpers — e.g. a migration that only DROPS an encrypted column):

    # ENCRYPTED_MIGRATION_HELPER_EXEMPT: <reason>

Use sparingly; the exemption is read on review.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

MIGRATION_DIR = "migrations/versions/"
ENCRYPTED_COLUMN = re.compile(r"\b\w+_encrypted\b")
HELPER_IMPORT = re.compile(
    r"from\s+core\.crypto\.migration_helpers\s+import|"
    r"import\s+core\.crypto\.migration_helpers"
)
EXEMPT_MARKER = "ENCRYPTED_MIGRATION_HELPER_EXEMPT"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()  # noqa: S603, S607


def _added_files(base: str, head: str) -> list[str]:
    try:
        out = _git("diff", "--name-only", "--diff-filter=A", f"{base}...{head}")
    except subprocess.CalledProcessError:
        out = _git("diff", "--name-only", "--diff-filter=A", base, head)
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    base = os.getenv("BASE_REF", "origin/main")
    head = os.getenv("HEAD_REF", "HEAD")

    added = _added_files(base, head)
    new_migrations = [
        f for f in added if f.startswith(MIGRATION_DIR) and f.endswith(".py")
    ]

    failures: list[tuple[str, str]] = []
    for path in new_migrations:
        body = Path(path).read_text(encoding="utf-8", errors="replace")
        if EXEMPT_MARKER in body:
            continue
        # Skip the helper module itself if it ever moved here.
        if "migration_helpers" in path:
            continue
        encrypted_hits = ENCRYPTED_COLUMN.findall(body)
        if not encrypted_hits:
            continue
        if not HELPER_IMPORT.search(body):
            cols = sorted(set(encrypted_hits))[:5]
            failures.append((path, ", ".join(cols)))

    if failures:
        print(
            "::error::Encrypted-column migration(s) skipping the hardening helpers.",
            file=sys.stderr,
        )
        for path, cols in failures:
            print(
                f"  - {path}\n      touches: {cols}",
                file=sys.stderr,
            )
        print(
            "\nWrap the migration in core.crypto.migration_helpers."
            "encrypted_migration(...).\n"
            "See docs/encrypted_column_migration_template.md.\n\n"
            f"Genuine exemptions: add a `# {EXEMPT_MARKER}: <reason>` "
            "line and the gate will skip the file (read on review).",
            file=sys.stderr,
        )
        return 1

    if new_migrations:
        print(
            f"OK: {len(new_migrations)} new migration(s) checked, "
            f"none needed the encrypted-column helper or all use it."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
