"""RLS coverage guard + tenant-session commit re-binding (audit 2026-09-13).

1. Every ORM table that carries a ``tenant_id`` column must be named by an
   ``ENABLE ROW LEVEL SECURITY`` statement in ``migrations/``. Pre-fix, 55
   such tables had no enforced policy on a freshly bootstrapped database.
2. ``get_tenant_session`` must re-issue the tenant ``set_config`` after any
   ``commit()`` — ``SET LOCAL`` semantics drop it at transaction end.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO / "migrations" / "versions"

_LITERAL_ENABLE = re.compile(r"ALTER TABLE ([a-z][a-z0-9_]*) ENABLE ROW LEVEL SECURITY")
_LOOP_ENABLE = re.compile(r"ALTER TABLE \{[a-z_]+\} ENABLE ROW LEVEL SECURITY")
_IDENTIFIER_LITERAL = re.compile(r"[\"']([a-z][a-z0-9_]+)[\"']")


def _load_migration(name: str) -> Any:
    path = MIGRATIONS / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _model_tenant_tables() -> set[str]:
    import core.models  # noqa: F401 — registers every ORM model
    import core.models.dsar  # noqa: F401 — not yet re-exported from core.models
    from core.models.base import BaseModel

    return {name for name, table in BaseModel.metadata.tables.items() if "tenant_id" in table.c}


def _rls_tables_declared_in_migrations() -> set[str]:
    covered: set[str] = set()
    for path in MIGRATIONS.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        if "ENABLE ROW LEVEL SECURITY" not in src:
            continue
        covered.update(_LITERAL_ENABLE.findall(src))
        if _LOOP_ENABLE.search(src):
            # Loop-style migrations (``for tbl in [...]: op.execute(f"ALTER
            # TABLE {tbl} ENABLE ...")``) — every quoted identifier in the
            # file is a candidate table name.
            covered.update(_IDENTIFIER_LITERAL.findall(src))
    return covered


def test_every_tenant_scoped_orm_table_has_an_rls_migration() -> None:
    tenant_tables = _model_tenant_tables()
    assert len(tenant_tables) >= 80, "ORM registry did not load"
    covered = _rls_tables_declared_in_migrations()
    missing = sorted(tenant_tables - covered)
    assert missing == [], (
        "tenant-scoped tables without an ENABLE ROW LEVEL SECURITY migration: "
        f"{missing} — add them to a migration (see v6_z16_rls_tenant_coverage.py)"
    )


def test_v6z16_migration_lists_only_real_tenant_tables_and_is_guarded() -> None:
    mig = _load_migration("v6_z16_rls_tenant_coverage.py")
    tenant_tables = _model_tenant_tables()
    # knowledge_chunk_sources is a raw-SQL (non-ORM) tenant table from the
    # v4.8.x knowledge migrations; it carries tenant_id and is covered too.
    stale = sorted(set(mig.ALL_TABLES) - tenant_tables - {"knowledge_chunk_sources"})
    assert stale == [], f"v6z16 names tables that are not tenant-scoped tables: {stale}"
    assert len(set(mig.ALL_TABLES)) == len(mig.ALL_TABLES) == 55
    assert len(mig.revision) <= 32
    assert mig.down_revision == "v6z15_tenant_plan_free"

    executed: list[str] = []
    with patch.object(mig.op, "execute", side_effect=executed.append):
        mig.upgrade()
    assert len(executed) == len(mig.ALL_TABLES)
    for statement in executed:
        # Safe on partial schemas: every table is catalog-guarded.
        assert statement.startswith("DO $$ BEGIN IF to_regclass('public.")
        assert "FORCE ROW LEVEL SECURITY" in statement
        assert "current_setting('agenticorg.tenant_id', true)" in statement
        assert "WITH CHECK" in statement
        # Legacy permissive/non-missing_ok policies must not OR-widen the new one.
        assert "DROP POLICY IF EXISTS company_isolation" in statement
        assert "DROP POLICY IF EXISTS tenant_isolation" in statement

    users_stmt = next(s for s in executed if "public.users'" in s)
    assert "COALESCE(current_setting('agenticorg.tenant_id', true), '') = ''" in users_stmt
    agents_stmt = next(s for s in executed if "public.agents'" in s)
    assert "COALESCE" not in agents_stmt
    atr_stmt = next(s for s in executed if "public.agent_task_results'" in s)
    assert "company_id::text = current_setting('agenticorg.company_id', true)" in atr_stmt


def test_v6z17_migration_adds_watermark_and_dsar_table_with_rls() -> None:
    mig = _load_migration("v6_z17_session_revocation_dsar.py")
    assert len(mig.revision) <= 32
    assert mig.down_revision == "v6z16_rls_coverage"
    executed: list[str] = []
    with patch.object(mig.op, "execute", side_effect=executed.append):
        mig.upgrade()
    joined = "\n".join(executed)
    assert "ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_invalid_before TIMESTAMPTZ" in joined
    assert "CREATE TABLE IF NOT EXISTS dsar_requests" in joined
    assert "ALTER TABLE dsar_requests FORCE ROW LEVEL SECURITY" in joined
    assert "CREATE POLICY dsar_requests_tenant_isolation" in joined


# ── get_tenant_session: commit must re-bind the RLS context ─────────────


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.params: list[dict[str, Any]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> None:
        self.calls.append(str(stmt))
        self.params.append(params or {})

    async def commit(self) -> None:
        self.calls.append("COMMIT")

    async def rollback(self) -> None:
        self.calls.append("ROLLBACK")

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _set_config_calls(session: _FakeSession) -> list[str]:
    return [c for c in session.calls if "set_config('agenticorg.tenant_id'" in c]


@pytest.mark.asyncio
async def test_get_tenant_session_rebinds_tenant_context_after_commit() -> None:
    import uuid

    from core import database

    fake = _FakeSession()
    tenant = uuid.uuid4()
    with patch.object(database, "async_session_factory", MagicMock(return_value=fake)):
        async with database.get_tenant_session(tenant) as session:
            assert len(_set_config_calls(fake)) == 1
            await session.commit()
            # Pre-fix: the SET LOCAL context was gone here and the handler
            # continued on an unscoped session.
            assert fake.calls[-3] == "COMMIT"
            assert len(_set_config_calls(fake)) == 2
            assert fake.params[-2] == {"tenant_id": str(tenant)}
            await session.commit()
            assert len(_set_config_calls(fake)) == 3

    # Context-manager exit commits once more without a dangling re-bind.
    assert fake.calls[-1] == "COMMIT"
    assert fake.calls.count("COMMIT") == 3


@pytest.mark.asyncio
async def test_get_tenant_session_rollback_on_error_still_rolls_back() -> None:
    import uuid

    from core import database

    fake = _FakeSession()
    with patch.object(database, "async_session_factory", MagicMock(return_value=fake)):
        with pytest.raises(RuntimeError):
            async with database.get_tenant_session(uuid.uuid4()):
                raise RuntimeError("boom")
    assert fake.calls[-1] == "ROLLBACK"
    assert "COMMIT" not in fake.calls


@pytest.mark.asyncio
async def test_get_tenant_session_rejects_malformed_ids() -> None:
    from core import database

    fake = _FakeSession()
    with patch.object(database, "async_session_factory", MagicMock(return_value=fake)):
        with pytest.raises(ValueError):
            async with database.get_tenant_session("not-a-uuid"):  # type: ignore[arg-type]
                pass
    assert _set_config_calls(fake) == []
