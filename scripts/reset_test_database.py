#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Drop and recreate the throwaway database the integration suite runs against.

``make test`` points the integration tests at a dedicated database on the
local stack's Postgres (``agenticorg_test`` by default) so they never touch the
development database: some of them drop and rebuild the ``public`` schema.

Refuses, with exit code 2, any URL whose database name does not end in
``_test`` or that does not use Postgres, so it cannot be aimed at a real
database by mistake.

    AGENTICORG_DB_URL=postgresql+asyncpg://user:pass@host:5432/agenticorg_test \\
        python scripts/reset_test_database.py
"""

from __future__ import annotations

import os
import re
import sys
from urllib.parse import unquote, urlsplit

REQUIRED_SUFFIX = "_test"
_ALLOWED_SCHEMES = frozenset({"postgresql", "postgresql+asyncpg", "postgresql+psycopg2", "postgres"})
_SAFE_NAME_RE = re.compile(r"[A-Za-z0-9_]+")


class UnsafeDatabaseURLError(ValueError):
    """The URL does not name a throwaway Postgres test database."""


def safe_test_database_name(url: str) -> str:
    """Return the database name in ``url`` if it is safe to drop, else raise."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeDatabaseURLError(f"URL cannot be parsed: {exc}") from exc
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeDatabaseURLError(f"not a Postgres URL (scheme {parts.scheme!r})")
    if not parts.hostname:
        raise UnsafeDatabaseURLError("URL has no host")
    if "," in parts.netloc or (port is None and parts.netloc.rstrip("]").endswith(":")):
        raise UnsafeDatabaseURLError("URL must name exactly one host")
    # Query parameters (dbname=, database=, options=, host=) and fragments can
    # redirect a driver to a different database than the path names.
    if parts.query or parts.fragment or "?" in url or "#" in url:
        raise UnsafeDatabaseURLError("URL must not carry query parameters or a fragment")
    raw = parts.path.lstrip("/")
    if "%" in raw:
        raise UnsafeDatabaseURLError("database name must not be percent-encoded")
    name = unquote(raw)
    if not name or "/" in name:
        raise UnsafeDatabaseURLError("URL does not name exactly one database")
    if not _SAFE_NAME_RE.fullmatch(name):
        raise UnsafeDatabaseURLError(f"database {name!r} contains characters other than ASCII letters, digits and '_'")
    if not name.endswith(REQUIRED_SUFFIX) or name == REQUIRED_SUFFIX:
        raise UnsafeDatabaseURLError(f"database {name!r} does not end in {REQUIRED_SUFFIX!r}; refusing to drop it")
    return name


def reset(url: str) -> str:
    import psycopg2  # noqa: PLC0415 - only needed when actually resetting
    from psycopg2 import sql  # noqa: PLC0415

    name = safe_test_database_name(url)
    parts = urlsplit(url)
    conn = psycopg2.connect(
        host=parts.hostname,
        port=parts.port or 5432,
        user=unquote(parts.username or ""),
        password=unquote(parts.password or ""),
        dbname="postgres",
        connect_timeout=10,
    )
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    finally:
        conn.close()
    return name


def main() -> int:
    url = os.environ.get("AGENTICORG_DB_URL", "")
    if not url:
        print("reset_test_database: AGENTICORG_DB_URL is not set", file=sys.stderr)
        return 2
    try:
        name = reset(url)
    except UnsafeDatabaseURLError as exc:
        print(f"reset_test_database: {exc}", file=sys.stderr)
        return 2
    print(f"reset_test_database: recreated {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
