# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Startup role-posture probe and the platform / tenant binding helpers (FINDINGS A-105)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest

import core.database as db

TENANT = "11111111-2222-4333-8444-555555555555"


class _Row:
    def __init__(self, row: tuple | None) -> None:
        self._row = row

    def first(self) -> tuple | None:
        return self._row


class _Conn:
    def __init__(self, row: tuple | None) -> None:
        self.row = row
        self.calls = 0

    async def execute(self, statement: Any, params: Any = None) -> _Row:
        self.calls += 1
        assert "pg_roles" in str(statement)
        return _Row(self.row)


@pytest.mark.asyncio
async def test_app_role_is_reported_enforced(monkeypatch, caplog) -> None:
    monkeypatch.delenv(db.SKIP_DB_ROLE_RLS_CHECK_ENV, raising=False)
    monkeypatch.delenv(db.REQUIRE_NOBYPASSRLS_ROLE_ENV, raising=False)
    with caplog.at_level(logging.INFO, logger="core.database"):
        posture = await db.verify_db_role_rls_posture(_Conn(("agenticorg_app", False, False)))
    assert posture is not None and posture.rls_enforced
    assert "DB_ROLE_BYPASSES_RLS" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [("postgres", True, False), ("ops", False, True)])
async def test_bypassing_role_warns_by_default(monkeypatch, caplog, row) -> None:
    monkeypatch.delenv(db.SKIP_DB_ROLE_RLS_CHECK_ENV, raising=False)
    monkeypatch.delenv(db.REQUIRE_NOBYPASSRLS_ROLE_ENV, raising=False)
    with caplog.at_level(logging.WARNING, logger="core.database"):
        posture = await db.verify_db_role_rls_posture(_Conn(row))
    assert posture is not None and not posture.rls_enforced
    assert "DB_ROLE_BYPASSES_RLS" in caplog.text
    assert all(record.levelno == logging.WARNING for record in caplog.records if "DB_ROLE" in record.getMessage())


@pytest.mark.asyncio
async def test_bypassing_role_is_refused_when_required(monkeypatch) -> None:
    monkeypatch.delenv(db.SKIP_DB_ROLE_RLS_CHECK_ENV, raising=False)
    monkeypatch.setenv(db.REQUIRE_NOBYPASSRLS_ROLE_ENV, "1")
    with pytest.raises(db.RuntimeSchemaError, match="row-level security"):
        await db.verify_db_role_rls_posture(_Conn(("postgres", True, False)))


@pytest.mark.asyncio
async def test_probe_can_be_skipped(monkeypatch) -> None:
    monkeypatch.setenv(db.SKIP_DB_ROLE_RLS_CHECK_ENV, "1")
    conn = _Conn(("postgres", True, False))
    assert await db.verify_db_role_rls_posture(conn) is None
    assert conn.calls == 0


class _Session:
    def __init__(self) -> None:
        self.statements: list[tuple[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, statement: Any, params: Any = None) -> None:
        self.statements.append((str(statement), params))

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_platform_session_marks_every_transaction(monkeypatch) -> None:
    session = _Session()
    monkeypatch.setattr(db, "current_session_factory", lambda: lambda: session)
    async with db.get_platform_session() as opened:
        assert opened is session
        await opened.commit()
    marks = [sql for sql, _ in session.statements if db.PLATFORM_CTX_GUC in sql]
    # Bound on open and re-bound after the explicit commit (set_config is transaction-local).
    assert len(marks) == 2
    assert all("agenticorg.tenant_id" not in sql for sql, _ in session.statements)
    assert session.commits == 2


@pytest.mark.asyncio
async def test_platform_session_rolls_back_on_error(monkeypatch) -> None:
    session = _Session()
    monkeypatch.setattr(db, "current_session_factory", lambda: lambda: session)
    with pytest.raises(RuntimeError):
        async with db.get_platform_session():
            raise RuntimeError("boom")
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_bind_tenant_context_validates_before_any_sql() -> None:
    session = _Session()
    with pytest.raises(ValueError):
        await db.bind_tenant_context(session, "not-a-uuid")
    with pytest.raises(ValueError):
        await db.bind_tenant_context(session, TENANT, "x' OR 1=1 --")
    assert session.statements == []
    await db.bind_tenant_context(session, uuid.UUID(TENANT))
    assert session.statements[0][1] == {"tenant_id": TENANT}
    assert session.statements[1][1] == {"company_id": ""}
