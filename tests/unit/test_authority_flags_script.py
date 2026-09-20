# SPDX-License-Identifier: Apache-2.0
"""``scripts/authority_flags.py`` - the operator tool for reserved feature flags.

The tenant session is faked; the Postgres behaviour is covered by
tests/integration/test_authority_flags_postgres.py.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from core.models.audit import AuditLog
from core.models.feature_flag import FeatureFlag
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

    async def execute(self, statement: Any) -> _Result:
        self.executed.append(statement)
        return _Result(self.rows)

    def add(self, row: Any) -> None:
        self.added.append(row)


def _run(argv: list[str], rows: list[Any] | None = None) -> tuple[int, _Session, list[uuid.UUID]]:
    session = _Session(rows or [])
    contexts: list[uuid.UUID] = []

    @asynccontextmanager
    async def _tenant_session(tenant_id: uuid.UUID, company_id: Any = None):
        contexts.append(tenant_id)
        yield session

    args = authority_flags.build_parser().parse_args(argv)
    with patch("core.database.get_tenant_session", _tenant_session):
        code = asyncio.run(authority_flags.run(args))
    return code, session, contexts


def _audits(session: _Session) -> list[AuditLog]:
    return [row for row in session.added if isinstance(row, AuditLog)]


def test_set_creates_an_enabled_row_and_a_signed_audit_row_with_the_operator():
    code, session, contexts = _run(
        ["set", "grants.enforce_closed.warn", "--tenant", TENANT, "--rollout", "50", "--operator", "ops-oncall"]
    )
    assert code == 0 and contexts == [uuid.UUID(TENANT)]
    [flag] = [row for row in session.added if isinstance(row, FeatureFlag)]
    assert (str(flag.tenant_id), flag.flag_key, flag.enabled, flag.rollout_percentage) == (
        TENANT,
        "grants.enforce_closed.warn",
        True,
        50,
    )
    [audit] = _audits(session)
    assert (audit.event_type, audit.actor_type, audit.actor_id, audit.action, audit.outcome) == (
        "feature_flag.authority_changed",
        "operator",
        "ops-oncall",
        "set",
        "applied",
    )
    assert audit.details == {
        "flag_key": "grants.enforce_closed.warn",
        "scope": TENANT,
        "before": None,
        "after": {"enabled": True, "rollout_percentage": 50},
    }
    assert audit.signature


def test_set_updates_an_existing_global_row_under_the_nil_tenant_context():
    existing = SimpleNamespace(enabled=False, rollout_percentage=0, description=None)
    code, session, contexts = _run(
        ["set", "grants.enforce_closed.deny", "--global", "--description", "rollout", "--operator", "ops"], [existing]
    )
    assert code == 0 and contexts == [authority_flags.GLOBAL_CONTEXT_TENANT]
    assert (existing.enabled, existing.rollout_percentage, existing.description) == (True, 100, "rollout")
    [audit] = _audits(session)
    assert audit.details["before"] == {"enabled": False, "rollout_percentage": 0}


def test_clear_deletes_the_row_and_audits_it():
    existing = SimpleNamespace(enabled=True, rollout_percentage=100, description=None)
    code, session, _ = _run(
        ["clear", "grants.enforce_closed.deny", "--tenant", TENANT, "--operator", "ops"], [existing]
    )
    assert code == 0
    assert any("DELETE" in str(statement).upper() for statement in session.executed)
    assert _audits(session)[0].outcome == "applied"


def test_clear_reports_when_no_row_existed(capsys):
    code, session, _ = _run(["clear", "grants.enforce_closed.deny", "--tenant", TENANT, "--operator", "ops"])
    assert code == 0
    assert "no row existed, nothing changed" in capsys.readouterr().out
    assert not any("DELETE" in str(statement).upper() for statement in session.executed)
    assert _audits(session)[0].outcome == "not_found"


def test_non_authority_keys_are_refused_before_touching_the_database():
    code, session, contexts = _run(["set", "new_workflow_builder", "--tenant", TENANT, "--operator", "ops"])
    assert code == 2 and session.added == [] and contexts == []


def test_a_blank_operator_is_refused():
    code, _, contexts = _run(["set", "grants.enforce_closed.warn", "--tenant", TENANT, "--operator", "  "])
    assert code == 2 and contexts == []


def test_changes_require_an_operator_and_a_scope():
    with pytest.raises(SystemExit):
        authority_flags.build_parser().parse_args(["set", "grants.enforce_closed.warn", "--tenant", TENANT])
    with pytest.raises(SystemExit):
        authority_flags.build_parser().parse_args(["set", "grants.enforce_closed.warn", "--operator", "ops"])


def test_list_prints_only_authority_flags(capsys):
    rows = [
        SimpleNamespace(flag_key="grants.enforce_closed.warn", enabled=True, rollout_percentage=100),
        SimpleNamespace(flag_key="new_workflow_builder", enabled=True, rollout_percentage=100),
    ]
    code, session, _ = _run(["list", "--tenant", TENANT], rows)
    out = capsys.readouterr().out
    assert code == 0 and session.added == []
    assert "grants.enforce_closed.warn enabled=True rollout=100" in out
    assert "new_workflow_builder" not in out


def test_main_parses_and_runs():
    seen: dict[str, Any] = {}

    async def _fake_run(args: Any) -> int:
        seen["command"] = args.command
        return 0

    with patch.object(authority_flags, "run", _fake_run):
        assert authority_flags.main(["list", "--global"]) == 0
    assert seen["command"] == "list"
