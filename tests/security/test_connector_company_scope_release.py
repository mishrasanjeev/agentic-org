"""Release regressions for exact-scope ConnectorConfig access."""

from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
COMPANY_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _RowsResult:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def scalars(self) -> _RowsResult:
        return self

    def all(self) -> list[Any]:
        return self.values


@pytest.mark.asyncio
async def test_tenant_global_oauth_and_commerce_queries_exclude_company_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.v1 import commerce_runtime, oauth_connector
    from core.connectors.provider_registry import get_provider

    oauth_statements: list[Any] = []

    class OAuthSession:
        async def execute(self, statement: Any) -> _ScalarResult:
            oauth_statements.append(statement)
            return _ScalarResult(None)

    @asynccontextmanager
    async def oauth_session(_tenant_id: uuid.UUID):
        yield OAuthSession()

    monkeypatch.setattr(oauth_connector, "get_tenant_session", oauth_session)
    result = await oauth_connector._payload_from_existing_connector_config(
        tenant_id=TENANT_ID,
        tenant_id_text=str(TENANT_ID),
        spec=get_provider("hubspot"),
    )

    assert result is None
    assert "connector_configs.company_id IS NULL" in str(oauth_statements[1])
    assert inspect.getsource(oauth_connector).count(
        "ConnectorConfig.company_id.is_(None)"
    ) >= 3

    commerce_statements: list[Any] = []

    class CommerceSession:
        async def execute(self, statement: Any) -> _RowsResult:
            commerce_statements.append(statement)
            return _RowsResult([])

    row = await commerce_runtime._load_shopify_connector_config_row(
        CommerceSession(),
        TENANT_ID,
        "merchant-1",
    )

    assert row is None
    assert "connector_configs.company_id IS NULL" in str(commerce_statements[0])
    assert inspect.getsource(commerce_runtime).count(
        "ConnectorConfig.company_id.is_(None)"
    ) >= 3


@pytest.mark.asyncio
async def test_cmo_connector_loader_uses_exact_scope_and_invalid_scope_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.v1 import kpis

    contexts: list[uuid.UUID | None] = []
    statements: list[Any] = []

    class Session:
        async def execute(self, statement: Any) -> _RowsResult:
            statements.append(statement)
            return _RowsResult([])

    @asynccontextmanager
    async def scoped_session(
        _tenant_id: str,
        company_id: uuid.UUID | None = None,
    ):
        contexts.append(company_id)
        yield Session()

    monkeypatch.setattr(kpis, "get_tenant_session", scoped_session)

    assert await kpis._load_marketing_connector_configs(
        str(TENANT_ID), str(COMPANY_ID)
    ) == []
    assert contexts == [COMPANY_ID]
    assert "connector_configs.company_id =" in str(statements[-1])

    assert await kpis._load_marketing_connector_configs(
        str(TENANT_ID), "default"
    ) == []
    assert contexts[-1] is None
    assert "connector_configs.company_id IS NULL" in str(statements[-1])

    context_count = len(contexts)
    assert await kpis._load_marketing_connector_configs(
        str(TENANT_ID), "not-a-uuid"
    ) == []
    assert len(contexts) == context_count


@pytest.mark.asyncio
async def test_token_refresh_enumerates_global_and_company_rls_contexts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.database as database
    from core.tasks import token_refresh

    raw_statements: list[Any] = []

    class RawSession:
        async def execute(self, statement: Any) -> None:
            raw_statements.append(statement)

        async def scalars(self, _statement: Any) -> _RowsResult:
            return _RowsResult([TENANT_ID])

    @asynccontextmanager
    async def raw_session_factory():
        yield RawSession()

    global_config = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        company_id=None,
        connector_name="hubspot",
        credentials_encrypted={},
        config={},
    )
    company_config = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        company_id=COMPANY_ID,
        connector_name="hubspot",
        credentials_encrypted={},
        config={},
    )
    contexts: list[uuid.UUID | None] = []

    class ScopedSession:
        def __init__(self, company_id: uuid.UUID | None) -> None:
            self.company_id = company_id

        async def scalars(self, statement: Any) -> _RowsResult:
            sql = str(statement)
            if "FROM companies" in sql:
                return _RowsResult([COMPANY_ID])
            if "FROM connector_configs" in sql:
                row = global_config if self.company_id is None else company_config
                return _RowsResult([row])
            raise AssertionError(sql)

    @asynccontextmanager
    async def scoped_session(
        _tenant_id: uuid.UUID,
        company_id: uuid.UUID | None = None,
    ):
        contexts.append(company_id)
        yield ScopedSession(company_id)

    monkeypatch.setattr(database, "async_session_factory", raw_session_factory)
    monkeypatch.setattr(database, "get_tenant_session", scoped_session)

    summary = await token_refresh._refresh_all()

    assert summary == {"refreshed": 0, "failed": 0, "skipped": 2}
    assert contexts == [None, COMPANY_ID]
    assert any("SET LOCAL row_security = off" in str(row) for row in raw_statements)


@pytest.mark.asyncio
async def test_secret_backfill_uses_global_rls_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.crypto import backfill_connector_secrets as backfill_module

    raw_statements: list[Any] = []

    class RawSession:
        async def execute(self, statement: Any) -> None:
            raw_statements.append(statement)

        async def scalars(self, _statement: Any) -> _RowsResult:
            return _RowsResult([TENANT_ID])

    @asynccontextmanager
    async def raw_session_factory():
        yield RawSession()

    connector = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        name="hubspot",
        auth_config={"client_secret": "legacy"},
    )
    contexts: list[uuid.UUID | None] = []
    config_statements: list[Any] = []

    class ConnectorSession:
        async def scalars(self, _statement: Any) -> _RowsResult:
            return _RowsResult([connector])

    class ExistingConfigSession:
        async def execute(self, statement: Any) -> _ScalarResult:
            config_statements.append(statement)
            return _ScalarResult(SimpleNamespace(id=uuid.uuid4()))

    @asynccontextmanager
    async def scoped_session(
        _tenant_id: uuid.UUID,
        company_id: uuid.UUID | None = None,
    ):
        contexts.append(company_id)
        if len(contexts) == 1:
            yield ConnectorSession()
        else:
            yield ExistingConfigSession()

    monkeypatch.setattr(
        backfill_module, "async_session_factory", raw_session_factory
    )
    monkeypatch.setattr(backfill_module, "get_tenant_session", scoped_session)

    summary = await backfill_module.backfill()

    assert summary == {"migrated": 0, "skipped": 1, "errors": 0}
    assert contexts == [None, None]
    assert "connector_configs.company_id IS NULL" in str(config_statements[0])
    assert any("SET LOCAL row_security = off" in str(row) for row in raw_statements)


def test_migration_and_configure_script_pin_exact_company_scope() -> None:
    migration = Path(
        "migrations/versions/v6_z6_connector_config_company_scope.py"
    ).read_text(encoding="utf-8")

    exact_scope = (
        "company_id IS NOT DISTINCT FROM\n"
        "                NULLIF(current_setting('agenticorg.company_id', true), '')::uuid"
    )
    assert migration.count(exact_scope) >= 2
    assert "DROP POLICY IF EXISTS tenant_isolation ON connector_configs" in migration
    assert "DROP POLICY IF EXISTS connector_configs_tenant_isolation" in migration
    assert "CREATE POLICY tenant_isolation ON connector_configs" in migration
    assert "ALTER TABLE connector_configs NO FORCE ROW LEVEL SECURITY" in migration

    from scripts import configure_cmo_vendor_sandbox_connectors as configure

    signature = inspect.signature(configure._upsert_connector_configs)
    assert "company_id" in signature.parameters
    source = inspect.getsource(configure._upsert_connector_configs)
    assert "ConnectorConfig.company_id == company_id" in source
    assert "ConnectorConfig.company_id.is_(None)" in source
    assert "company_id=company_id" in source
