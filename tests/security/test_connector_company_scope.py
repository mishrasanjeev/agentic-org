"""Security regression tests for company-scoped connector execution."""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import api.v1.agents as agents_module
import core.database as database_module
import core.tool_gateway.gateway as gateway_module
from api.v1.a2a import A2ATaskRequest
from api.v1.mcp import MCPCallRequest
from core.langgraph.runner import resume_agent
from core.models.connector_config import ConnectorConfig
from core.tool_gateway.gateway import ToolGateway

TENANT_ID = "11111111-1111-1111-1111-111111111111"
COMPANY_A = "22222222-2222-2222-2222-222222222222"
COMPANY_B = "33333333-3333-3333-3333-333333333333"
FOREIGN_COMPANY = "44444444-4444-4444-4444-444444444444"


@pytest.mark.asyncio
async def test_gateway_cache_and_registration_are_company_separated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_module.settings, "env", "production")
    connector_a = AsyncMock()
    connector_b = AsyncMock()
    connector_a.execute_tool.return_value = {"company": "A"}
    connector_b.execute_tool.return_value = {"company": "B"}

    gateway = ToolGateway()
    gateway.register_connector(
        "zoho_books",
        connector_a,
        tenant_id=TENANT_ID,
        company_id=COMPANY_A,
    )
    gateway.register_connector(
        "zoho_books",
        connector_b,
        tenant_id=TENANT_ID,
        company_id=COMPANY_B,
    )

    common = {
        "tenant_id": TENANT_ID,
        "agent_id": "agent-1",
        "agent_scopes": ["tool:zoho_books:read:trial_balance"],
        "connector_name": "zoho_books",
        "tool_name": "get_trial_balance",
        "params": {},
        "domain": "finance",
    }
    result_a = await gateway.execute(company_id=COMPANY_A, **common)
    result_b = await gateway.execute(company_id=COMPANY_B, **common)

    assert result_a == {"company": "A"}
    assert result_b == {"company": "B"}
    connector_a.execute_tool.assert_awaited_once()
    connector_b.execute_tool.assert_awaited_once()
    assert (TENANT_ID, COMPANY_A, "zoho_books") in gateway._connectors
    assert (TENANT_ID, COMPANY_B, "zoho_books") in gateway._connectors


@pytest.mark.asyncio
async def test_company_request_never_falls_back_to_global_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_module.settings, "env", "production")
    global_connector = AsyncMock()
    gateway = ToolGateway()
    gateway.register_connector("zoho_books", global_connector)
    resolver = AsyncMock(return_value=None)
    monkeypatch.setattr(gateway, "_resolve_connector", resolver)

    result = await gateway.execute(
        tenant_id=TENANT_ID,
        agent_id="agent-1",
        agent_scopes=["tool:zoho_books:read:trial_balance"],
        connector_name="zoho_books",
        tool_name="get_trial_balance",
        params={},
        company_id=COMPANY_A,
        domain="finance",
    )

    assert result["error"]["code"] == "E1005"
    global_connector.execute_tool.assert_not_awaited()
    resolver.assert_awaited_once_with(TENANT_ID, COMPANY_A, "zoho_books")


@pytest.mark.asyncio
async def test_dynamic_resolver_never_instantiates_after_config_store_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from connectors.registry import ConnectorRegistry

    constructed = 0

    class ProviderConnector:
        def __init__(self, *, config: dict[str, object]) -> None:
            nonlocal constructed
            constructed += 1

        async def connect(self) -> None:
            return None

    @asynccontextmanager
    async def failing_session(_tenant_id: object, company_id: object = None):
        del company_id
        raise RuntimeError("credential store unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(ConnectorRegistry, "get", lambda _name: ProviderConnector)
    monkeypatch.setattr(database_module, "get_tenant_session", failing_session)

    gateway = ToolGateway()
    connector = await gateway._resolve_connector(TENANT_ID, COMPANY_A, "zoho_books")

    assert connector is None
    assert constructed == 0


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _Session:
    def __init__(self, value: object) -> None:
        self.value = value

    async def execute(self, _query: object) -> _ScalarResult:
        return _ScalarResult(self.value)


@pytest.mark.asyncio
async def test_connector_config_resolver_uses_company_a_and_b_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {
        COMPANY_A: SimpleNamespace(
            connector_name="zoho_books",
            config={"organization_id": "org-a"},
            credentials_encrypted={"access_token": "token-a"},
        ),
        COMPANY_B: SimpleNamespace(
            connector_name="zoho_books",
            config={"organization_id": "org-b"},
            credentials_encrypted={"access_token": "token-b"},
        ),
    }

    @asynccontextmanager
    async def fake_session(_tenant_id: object, company_id: object = None):
        company_key = str(company_id) if company_id is not None else None
        if company_key is None:
            yield _Session(COMPANY_A)
        else:
            yield _Session(rows[company_key])

    monkeypatch.setattr(database_module, "get_tenant_session", fake_session)

    config_a, names_a = await agents_module._resolve_connector_configs(
        tenant_id=TENANT_ID,
        connector_ids=["registry-zoho_books"],
        company_id=COMPANY_A,
    )

    @asynccontextmanager
    async def fake_session_b(_tenant_id: object, company_id: object = None):
        company_key = str(company_id) if company_id is not None else None
        if company_key is None:
            yield _Session(COMPANY_B)
        else:
            yield _Session(rows[company_key])

    monkeypatch.setattr(database_module, "get_tenant_session", fake_session_b)
    config_b, names_b = await agents_module._resolve_connector_configs(
        tenant_id=TENANT_ID,
        connector_ids=["registry-zoho_books"],
        company_id=COMPANY_B,
    )

    assert config_a == {"organization_id": "org-a", "access_token": "token-a"}
    assert config_b == {"organization_id": "org-b", "access_token": "token-b"}
    assert names_a == names_b == ["zoho_books"]


@pytest.mark.asyncio
async def test_foreign_company_is_rejected_before_connector_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def fake_session(_tenant_id: object, company_id: object = None):
        del company_id
        yield _Session(None)

    monkeypatch.setattr(agents_module, "get_tenant_session", fake_session)

    with pytest.raises(HTTPException) as exc_info:
        await agents_module._require_company_for_tenant(TENANT_ID, FOREIGN_COMPANY)

    assert exc_info.value.status_code == 404


def test_connector_config_model_has_scope_aware_uniqueness() -> None:
    assert ConnectorConfig.__table__.c.company_id.nullable is True
    assert {fk.target_fullname for fk in ConnectorConfig.__table__.c.company_id.foreign_keys} == {
        "companies.id"
    }
    index_names = {index.name for index in ConnectorConfig.__table__.indexes}
    assert "uq_connector_configs_tenant_global" in index_names
    assert "uq_connector_configs_tenant_company" in index_names
    assert "ix_connector_configs_tenant_company" in index_names


def test_external_entrypoints_and_resume_carry_company_context() -> None:
    assert "company_id" in MCPCallRequest.model_fields
    assert "company_id" in A2ATaskRequest.model_fields
    resume_parameters = inspect.signature(resume_agent).parameters
    assert "tenant_id" in resume_parameters
    assert "company_id" in resume_parameters
    assert "domain" in resume_parameters
