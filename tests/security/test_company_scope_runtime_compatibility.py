"""Regression coverage for exact company-scoped connector runtime access."""

from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import api.v1.agents as agents_module
import core.database as database_module
import core.marketing.weekly_report_sandbox_pilot as sandbox_module
from core.schemas.api import AgentCreate

TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
COMPANY_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


class _StopAfterReadinessError(RuntimeError):
    """Stop a lifecycle call after proving which session reached its gate."""


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


def _tenant_and_company_sessions(
    tenant_session: Any,
    company_session: Any,
    calls: list[tuple[uuid.UUID, uuid.UUID | None]],
):
    @asynccontextmanager
    async def _scope(
        tenant_id: uuid.UUID,
        company_id: uuid.UUID | None = None,
    ):
        calls.append((tenant_id, company_id))
        yield company_session if company_id is not None else tenant_session

    return _scope


@pytest.mark.asyncio
async def test_active_agent_create_uses_exact_company_session_for_connector_gate() -> None:
    tenant_session = MagicMock()
    tenant_session.execute = AsyncMock(return_value=_ScalarResult(COMPANY_ID))
    company_session = MagicMock()
    calls: list[tuple[uuid.UUID, uuid.UUID | None]] = []
    scope = _tenant_and_company_sessions(tenant_session, company_session, calls)
    readiness = AsyncMock(side_effect=_StopAfterReadinessError)
    body = AgentCreate(
        name="Scoped active agent",
        agent_type="company_scope_test",
        domain="test",
        company_id=str(COMPANY_ID),
        initial_status="active",
        connector_ids=[],
    )

    with (
        patch.object(agents_module, "get_tenant_session", new=scope),
        patch.object(
            agents_module,
            "_assert_connectors_ready_for_activation",
            new=readiness,
        ),
        pytest.raises(_StopAfterReadinessError),
    ):
        await agents_module.create_agent(body, str(TENANT_ID))

    assert calls == [(TENANT_ID, None), (TENANT_ID, COMPANY_ID)]
    readiness.assert_awaited_once_with(
        company_session,
        TENANT_ID,
        [],
        COMPANY_ID,
    )


@pytest.mark.asyncio
async def test_resume_uses_authoritative_agent_company_for_connector_gate() -> None:
    agent_id = uuid.uuid4()
    agent = SimpleNamespace(
        id=agent_id,
        status="paused",
        company_id=COMPANY_ID,
        config={},
        connector_ids=[],
    )
    pause_event = SimpleNamespace(from_status="active")
    tenant_session = MagicMock()
    tenant_session.execute = AsyncMock(
        side_effect=[_ScalarResult(agent), _ScalarResult(pause_event)]
    )
    company_session = MagicMock()
    calls: list[tuple[uuid.UUID, uuid.UUID | None]] = []
    scope = _tenant_and_company_sessions(tenant_session, company_session, calls)
    readiness = AsyncMock(side_effect=_StopAfterReadinessError)

    with (
        patch.object(agents_module, "get_tenant_session", new=scope),
        patch.object(
            agents_module,
            "_assert_connectors_ready_for_activation",
            new=readiness,
        ),
        pytest.raises(_StopAfterReadinessError),
    ):
        await agents_module.resume_agent(agent_id, str(TENANT_ID))

    assert calls == [(TENANT_ID, None), (TENANT_ID, COMPANY_ID)]
    readiness.assert_awaited_once_with(
        company_session,
        TENANT_ID,
        [],
        COMPANY_ID,
    )


@pytest.mark.asyncio
async def test_promote_uses_authoritative_agent_company_for_connector_gate() -> None:
    agent_id = uuid.uuid4()
    agent = SimpleNamespace(
        id=agent_id,
        status="shadow",
        company_id=COMPANY_ID,
        config={},
        connector_ids=[],
        shadow_min_samples=0,
    )
    tenant_session = MagicMock()
    tenant_session.execute = AsyncMock(return_value=_ScalarResult(agent))
    company_session = MagicMock()
    calls: list[tuple[uuid.UUID, uuid.UUID | None]] = []
    scope = _tenant_and_company_sessions(tenant_session, company_session, calls)
    readiness = AsyncMock(side_effect=_StopAfterReadinessError)

    with (
        patch.object(agents_module, "get_tenant_session", new=scope),
        patch.object(
            agents_module,
            "_assert_connectors_ready_for_activation",
            new=readiness,
        ),
        pytest.raises(_StopAfterReadinessError),
    ):
        await agents_module.promote_agent(agent_id, str(TENANT_ID))

    assert calls == [(TENANT_ID, None), (TENANT_ID, COMPANY_ID)]
    readiness.assert_awaited_once_with(
        company_session,
        TENANT_ID,
        [],
        COMPANY_ID,
    )


class _SyncResult:
    def fetchall(self) -> list[Any]:
        return []


class _SyncConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def execute(self, statement: Any, params: dict[str, str]) -> _SyncResult:
        self.calls.append((str(statement), params))
        return _SyncResult()


class _SyncConnectionContext:
    def __init__(self, connection: _SyncConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _SyncConnection:
        return self.connection

    def __exit__(self, *_args: Any) -> None:
        return None


class _SyncEngine:
    def __init__(self) -> None:
        self.connection = _SyncConnection()
        self.disposed = False

    def connect(self) -> _SyncConnectionContext:
        return _SyncConnectionContext(self.connection)

    def dispose(self) -> None:
        self.disposed = True


@pytest.mark.parametrize(
    ("company_id", "company_scope"),
    [(COMPANY_ID, str(COMPANY_ID)), (None, "")],
)
def test_weekly_report_sync_query_sets_exact_rls_context(
    company_id: uuid.UUID | None,
    company_scope: str,
) -> None:
    engine = _SyncEngine()
    with patch("sqlalchemy.create_engine", return_value=engine):
        rows = sandbox_module._load_connector_config_rows(
            TENANT_ID,
            company_id,
            "postgresql://user:pass@localhost/db",
        )

    assert rows == []
    assert engine.disposed is True
    assert len(engine.connection.calls) == 3
    tenant_sql, tenant_params = engine.connection.calls[0]
    company_sql, company_params = engine.connection.calls[1]
    query_sql, query_params = engine.connection.calls[2]
    assert "set_config('agenticorg.tenant_id'" in tenant_sql
    assert tenant_params == {"tenant_id": str(TENANT_ID)}
    assert "set_config('agenticorg.company_id'" in company_sql
    assert company_params == {"company_id": company_scope}
    assert "company_id IS NOT DISTINCT FROM" in query_sql
    assert "CAST(NULLIF(:company_id, '') AS UUID)" in query_sql
    assert query_params == {
        "tenant_id": str(TENANT_ID),
        "company_id": company_scope,
    }


def test_weekly_report_discovery_passes_company_scope_to_db_loader() -> None:
    loader = MagicMock(return_value=[])
    with patch.object(sandbox_module, "_load_connector_config_rows", new=loader):
        result = sandbox_module._discover_connector_config_categories(
            env={},
            tenant_id=str(TENANT_ID),
            company_id=str(COMPANY_ID),
            db_url="postgresql://user:pass@localhost/db",
            connector_rows=None,
        )

    assert result["state"] == "ready"
    loader.assert_called_once_with(
        TENANT_ID,
        COMPANY_ID,
        "postgresql://user:pass@localhost/db",
    )


def test_local_startup_connector_policy_matches_exact_company_scope() -> None:
    source = inspect.getsource(
        database_module._legacy_startup_schema_repair_for_local_only
    )
    connector_repair = source.split(
        "# v5.0.0: Ensure connector_configs table exists.", 1
    )[1].split("# v4.3.0: gstn_auto_upload flag on companies", 1)[0]

    assert "ALTER TABLE connector_configs ENABLE ROW LEVEL SECURITY" in connector_repair
    assert "ALTER TABLE connector_configs FORCE ROW LEVEL SECURITY" in connector_repair
    assert "DROP POLICY IF EXISTS tenant_isolation ON connector_configs" in connector_repair
    assert "DROP POLICY IF EXISTS connector_configs_tenant_isolation " in connector_repair
    assert connector_repair.count("DROP POLICY IF EXISTS") == 3
    assert "CREATE POLICY connector_configs_scope_isolation" in connector_repair
    assert connector_repair.count("company_id IS NOT DISTINCT FROM") == 2
    assert connector_repair.count("# noqa: S608  # nosec B608") == 0
    assert source.count("# noqa: S608  # nosec B608") == 4
