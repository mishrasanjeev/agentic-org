"""Exact-scope RLS coverage for crypto verification and rewrap maintenance."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.crypto import rewrap, verify_all

TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
COMPANY_A = uuid.UUID("22222222-2222-2222-2222-222222222222")
COMPANY_B = uuid.UUID("33333333-3333-3333-3333-333333333333")


class _ScalarResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list[object]:
        return self._values


class _ColumnResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def all(self) -> list[tuple[object]]:
        return [(value,) for value in self._values]


@asynccontextmanager
async def _yield_session(session):
    yield session


def test_scope_discovery_sets_row_security_off_before_tenant_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_sql: list[str] = []
    tenant_contexts: list[tuple[uuid.UUID, uuid.UUID | None]] = []

    class _RawSession:
        async def execute(self, statement):
            raw_sql.append(str(statement))
            if "row_security" in str(statement):
                return MagicMock()
            return _ScalarResult([TENANT_ID])

    class _CompanySession:
        async def execute(self, _statement):
            return _ScalarResult([COMPANY_A, COMPANY_B])

    def tenant_session(tenant_id, company_id=None):
        tenant_contexts.append((tenant_id, company_id))
        return _yield_session(_CompanySession())

    monkeypatch.setattr(
        verify_all,
        "async_session_factory",
        lambda: _yield_session(_RawSession()),
    )
    monkeypatch.setattr(verify_all, "get_tenant_session", tenant_session)

    scopes = asyncio.run(verify_all.discover_tenant_company_scopes())

    assert "SET LOCAL row_security = off" in raw_sql[0]
    assert "tenants" in raw_sql[1]
    assert tenant_contexts == [(TENANT_ID, None)]
    assert scopes == [
        verify_all.TenantCompanyScope(TENANT_ID, None),
        verify_all.TenantCompanyScope(TENANT_ID, COMPANY_A),
        verify_all.TenantCompanyScope(TENANT_ID, COMPANY_B),
    ]


def test_verify_all_scans_global_and_each_company_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scopes = [
        verify_all.TenantCompanyScope(TENANT_ID, None),
        verify_all.TenantCompanyScope(TENANT_ID, COMPANY_A),
        verify_all.TenantCompanyScope(TENANT_ID, COMPANY_B),
    ]
    seen_contexts: list[tuple[uuid.UUID, uuid.UUID | None]] = []
    statements: list[str] = []
    ciphertext = {
        None: {"_encrypted": "agko_vglobal$ciphertext"},
        COMPANY_A: {"_encrypted": "agko_vcompany-a$ciphertext"},
        COMPANY_B: {"_encrypted": "agko_vcompany-b$ciphertext"},
    }

    class _ScopedSession:
        def __init__(self, company_id: uuid.UUID | None) -> None:
            self.company_id = company_id

        async def execute(self, statement):
            statements.append(str(statement))
            return _ColumnResult([ciphertext[self.company_id]])

    def tenant_session(tenant_id, company_id=None):
        seen_contexts.append((tenant_id, company_id))
        return _yield_session(_ScopedSession(company_id))

    async def discover():
        return scopes

    monkeypatch.setattr(
        verify_all,
        "_SCANNERS",
        [
            (
                "connector_configs.credentials_encrypted",
                "core.models.connector_config:ConnectorConfig:credentials_encrypted",
            )
        ],
    )
    monkeypatch.setattr(verify_all, "discover_tenant_company_scopes", discover)
    monkeypatch.setattr(verify_all, "get_tenant_session", tenant_session)

    refs = asyncio.run(verify_all.scan_encrypted_columns_all_scopes())

    assert seen_contexts == [
        (TENANT_ID, None),
        (TENANT_ID, COMPANY_A),
        (TENANT_ID, COMPANY_B),
    ]
    assert {ref.ref for ref in refs["connector_configs.credentials_encrypted"]} == {
        "global",
        "company-a",
        "company-b",
    }
    assert all("tenant_id" in statement for statement in statements)
    assert "company_id IS NULL" in statements[0]
    assert all("company_id" in statement for statement in statements[1:])

    # Key retirement cannot be approved from a caller's narrower session:
    # the Company B reference found by the all-scope scan must block it.
    with pytest.raises(verify_all.KeyStillReferencedError, match="company-b"):
        asyncio.run(verify_all.assert_key_unreferenced("company-b", object()))


def test_verify_all_scope_discovery_failure_is_not_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_discovery():
        raise RuntimeError("tenant catalog unavailable")

    monkeypatch.setattr(verify_all, "discover_tenant_company_scopes", fail_discovery)

    with pytest.raises(RuntimeError, match="tenant catalog unavailable"):
        asyncio.run(verify_all.scan_encrypted_columns_all_scopes())


def test_rewrap_scoped_update_requires_exactly_one_visible_row() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.rowcount = 0
    session.execute.return_value = result
    scope = verify_all.TenantCompanyScope(TENANT_ID, COMPANY_A)

    with pytest.raises(RuntimeError, match="did not affect exactly one row"):
        asyncio.run(
            rewrap._update_row(
                session,
                "connector_configs.credentials_encrypted",
                uuid.UUID("44444444-4444-4444-4444-444444444444"),
                {"_encrypted": "agko_vv2$ciphertext"},
                scope=scope,
                exact_company_scope=True,
            )
        )

    statement = str(session.execute.await_args.args[0])
    assert "tenant_id = CAST(:tenant_id AS UUID)" in statement
    assert "company_id IS NOT DISTINCT FROM CAST(:company_id AS UUID)" in statement


def test_rewrap_verify_discovery_failure_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTICORG_VAULT_KEYRING", "v2:active,v1:older")
    monkeypatch.setattr(
        rewrap,
        "_SCANNERS",
        [("connector_configs.credentials_encrypted", "ignored")],
    )

    async def fail_plan(_columns):
        raise RuntimeError("scope discovery failed")

    monkeypatch.setattr(rewrap, "_build_scope_plan", fail_plan)
    assert asyncio.run(rewrap.verify(None)) == 1
