# SPDX-License-Identifier: Apache-2.0
"""Build an empty database's starting schema for ``alembic upgrade``.

The Alembic chain does not start from nothing. Its first revision,
``v400_apex``, alters a schema that was created before Alembic existed (the
raw SQL files ``migrations/001_*.sql`` onwards, then ``init_db()``), and those
files no longer apply to a current Postgres. Every environment since the
Alembic cutover therefore starts from the ORM schema stamped at
``v480_baseline``; the revisions after the baseline are written to be
idempotent against that shape (``migrations/README.md``).

``migrations/env.py`` takes the migration advisory lock, then calls
:func:`plan_empty_database_bootstrap` before an
upgrade and, when it returns ``True``, :func:`create_orm_baseline` and a stamp
at :data:`BASELINE_REVISION`, so a bare ``alembic upgrade head`` on an empty
database follows the same path as ``scripts/alembic_migrate.py``. Anything it
cannot handle safely is refused with a reason code instead of being guessed.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING

from alembic.script.revision import RevisionError
from alembic.util import CommandError
from sqlalchemy import text

if TYPE_CHECKING:
    from alembic.script import ScriptDirectory
    from sqlalchemy.engine import Connection

BASELINE_REVISION = "v480_baseline"
ALEMBIC_VERSION_TABLE = "alembic_version"

REASON_UNMANAGED_DATABASE = "unmanaged_database_not_empty"
REASON_TARGET_BEFORE_BASELINE = "target_before_baseline"
REASON_TARGET_UNRESOLVED = "target_unresolved"


class EmptyDatabaseBootstrapError(RuntimeError):
    """An upgrade cannot start on this database; ``reason`` says why."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason


def target_reaches_baseline(script: ScriptDirectory, destination: object) -> bool | None:
    """Whether upgrading to ``destination`` passes through the baseline.

    Returns ``None`` when the destination cannot be resolved to concrete
    revisions without a starting point (relative steps such as ``+1``, or
    branch-qualified identifiers), and ``False`` for ``base``.
    """
    if destination is None:
        return False
    targets = (destination,) if isinstance(destination, str) else tuple(destination)  # type: ignore[call-overload]
    for target in targets:
        if not isinstance(target, str) or target.startswith(("+", "-")) or "@" in target:
            return None
    try:
        revisions = {script_rev.revision for script_rev in script.iterate_revisions(destination, "base")}  # type: ignore[arg-type]
    except (CommandError, RevisionError):
        # An unknown revision: the caller refuses it as unresolved.
        return None
    return BASELINE_REVISION in revisions


RELATION_NAMES_SQL = """
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = current_schema()
      AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
"""


def existing_relations(connection: Connection) -> list[str]:
    """Every relation in the target schema, not only the ordinary tables.

    A database holding a view, a materialised view, a sequence or a foreign
    table is not empty: creating the ORM baseline in it can collide with what
    is already there, so the bootstrap must refuse it rather than treat it as
    a clean database.
    """
    return list(connection.execute(text(RELATION_NAMES_SQL)).scalars())


def plan_empty_database_bootstrap(
    *,
    command: str | None,
    current_heads: Collection[str],
    table_names: Collection[str],
    reaches_baseline: bool | None,
) -> bool:
    """Decide whether an Alembic run must first build the baseline schema.

    Only an ``upgrade`` of a database with no recorded revision is considered.
    An empty database whose target includes the baseline is bootstrapped. A
    database that has tables but no revision, or a target that stops before
    the baseline (or cannot be resolved), is refused: running the chain from
    ``v400_apex`` would fail part-way with an unrelated error.
    """
    if command != "upgrade" or current_heads:
        return False
    existing = sorted(set(table_names) - {ALEMBIC_VERSION_TABLE})
    if existing:
        raise EmptyDatabaseBootstrapError(
            REASON_UNMANAGED_DATABASE,
            "the database holds relations but no Alembic revision (for example "
            f"{', '.join(existing[:5])}). Run `python scripts/alembic_migrate.py`, "
            f"which stamps a legacy schema at {BASELINE_REVISION} before upgrading.",
        )
    if reaches_baseline is None:
        raise EmptyDatabaseBootstrapError(
            REASON_TARGET_UNRESOLVED,
            "an empty database can only be upgraded to a named revision at or after "
            f"{BASELINE_REVISION} (for example `head`), not a relative or branch target.",
        )
    if not reaches_baseline:
        raise EmptyDatabaseBootstrapError(
            REASON_TARGET_BEFORE_BASELINE,
            f"revisions before {BASELINE_REVISION} alter a pre-Alembic schema and cannot "
            "build an empty database. Upgrade to `head` instead.",
        )
    return True


def create_orm_baseline(connection: Connection) -> None:
    """Create the extensions and ORM tables the baseline revision assumes."""
    import core.models  # noqa: F401, PLC0415 - register every ORM model
    from core.models.base import BaseModel  # noqa: PLC0415

    connection.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    BaseModel.metadata.create_all(connection)
