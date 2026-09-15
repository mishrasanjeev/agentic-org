# SPDX-License-Identifier: Apache-2.0
"""``scripts/authority_flags.py`` - the operator tool for reserved feature flags.

The database is faked at the session boundary; the Postgres behaviour is
covered by tests/integration/test_authority_flags_postgres.py.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts import authority_flags

TENANT = str(uuid.UUID(int=0x1F6A))


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.added: list[Any] = []
        self.executed: list[Any] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, statement: Any) -> _Result:
        self.executed.append(statement)
        return _Result(self.rows)

    def add(self, row: Any) -> None:
        self.added.append(row)


def _run(argv: list[str], rows: list[Any] | None = None) -> tuple[int, _Session]:
    import asyncio

    session = _Session(rows or [])
    engine = MagicMock()
    engine.dispose = AsyncMock()
    args = authority_flags.build_parser().parse_args(argv)
    with (
        patch.object(authority_flags, "create_async_engine", return_value=engine),
        patch.object(authority_flags, "async_sessionmaker", return_value=lambda: session),
    ):
        code = asyncio.run(authority_flags.run(args, "postgresql+asyncpg://placeholder/db"))
    engine.dispose.assert_awaited_once()
    return code, session


def test_set_creates_an_enabled_row_for_the_tenant():
    code, session = _run(["set", "grants.enforce_closed.warn", "--tenant", TENANT, "--rollout", "50"])
    assert code == 0
    [row] = session.added
    assert (str(row.tenant_id), row.flag_key, row.enabled, row.rollout_percentage) == (
        TENANT,
        "grants.enforce_closed.warn",
        True,
        50,
    )


def test_set_updates_an_existing_global_row():
    existing = SimpleNamespace(enabled=False, rollout_percentage=0, description=None)
    code, session = _run(["set", "grants.enforce_closed.deny", "--global", "--description", "rollout"], [existing])
    assert code == 0 and session.added == []
    assert (existing.enabled, existing.rollout_percentage, existing.description) == (True, 100, "rollout")


def test_clear_deletes_the_row():
    code, session = _run(["clear", "grants.enforce_closed.deny", "--tenant", TENANT])
    assert code == 0
    assert any("DELETE" in str(statement).upper() for statement in session.executed)


def test_non_authority_keys_are_refused():
    code, session = _run(["set", "new_workflow_builder", "--tenant", TENANT])
    assert code == 2 and session.added == []


def test_list_prints_only_authority_flags(capsys):
    rows = [
        SimpleNamespace(flag_key="grants.enforce_closed.warn", enabled=True, rollout_percentage=100),
        SimpleNamespace(flag_key="new_workflow_builder", enabled=True, rollout_percentage=100),
    ]
    code, _ = _run(["list", "--tenant", TENANT], rows)
    out = capsys.readouterr().out
    assert code == 0
    assert "grants.enforce_closed.warn enabled=True rollout=100" in out
    assert "new_workflow_builder" not in out


def test_a_scope_is_required():
    with pytest.raises(SystemExit):
        authority_flags.build_parser().parse_args(["set", "grants.enforce_closed.warn"])


def test_main_reads_the_configured_database_url(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "db_url", "postgresql+asyncpg://placeholder/configured")
    seen: dict[str, Any] = {}

    async def _fake_run(args: Any, db_url: str) -> int:
        seen["db_url"] = db_url
        return 0

    with patch.object(authority_flags, "run", _fake_run):
        assert authority_flags.main(["list", "--global"]) == 0
    assert seen["db_url"] == "postgresql+asyncpg://placeholder/configured"
