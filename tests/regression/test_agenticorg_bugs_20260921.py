"""Regression coverage for the 2026-09-21 AgenticOrg bug sheet.

These tests pin the producer contracts behind the two reported failures:

* connector readiness and connector config resolution use the same tenant
  global fallback when an agent carries a company binding;
* natural-language agent generation forwards the authenticated tenant to the
  provider resolver, so a tenant-owned Gemini/Claude/OpenAI credential is
  actually eligible for use.

The doubles are deliberately tenant-scoped and never contain real secrets.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

TENANT_ID = uuid.UUID("49ca24aa-c6e7-4124-91af-059023295da4")
COMPANY_ID = uuid.UUID("56e585f7-b283-4743-9889-7e264cf4515e")
OTHER_COMPANY_ID = uuid.UUID("9c7b4e3f-7f5f-4b9f-bd9f-7f9dc4e7f6b1")


def _valid_generation_response() -> MagicMock:
    response = MagicMock()
    response.content = json.dumps(
        {
            "suggestions": [
                {
                    "confidence": 0.95,
                    "agent_type": "ap_processor",
                    "domain": "finance",
                    "employee_name": "Test AP Agent",
                    "designation": "AP Processing Specialist",
                    "suggested_tools": ["fetch_bank_statement"],
                    "system_prompt": "Process invoices and flag discrepancies for review.",
                    "confidence_floor": 0.88,
                    "hitl_condition": "confidence < 0.88",
                    "specialization": "Invoice review",
                }
            ]
        }
    )
    response.model = "gemini-2.5-flash"
    response.tokens_used = 1
    return response


@pytest.mark.asyncio
async def test_agent_generation_forwards_authenticated_tenant_to_router() -> None:
    """BUG-02: generation must resolve the caller tenant's LLM credential."""
    from core.agent_generator import generate_agent_config

    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=_valid_generation_response())

    result = await generate_agent_config(
        "Create an accounts-payable employee who reviews invoices",
        llm=llm,
        tenant_id=str(TENANT_ID),
    )

    assert result["suggestions"][0]["domain"] == "finance"
    call = llm.complete.await_args
    assert call.kwargs["tenant_id"] == str(TENANT_ID)


@pytest.mark.asyncio
async def test_router_resolves_tenant_owned_provider_credential() -> None:
    """BUG-02: router credential lookup must be tenant-aware and secret-safe."""
    from core.ai_providers.resolver import ResolvedCredential
    from core.llm.router import LLMRouter

    resolved = ResolvedCredential(
        secret="synthetic-tenant-secret",
        provider="gemini",
        kind="llm",
        source="tenant",
    )
    with patch(
        "core.ai_providers.resolver.get_provider_credential",
        new=AsyncMock(return_value=resolved),
    ) as resolver:
        secret = await LLMRouter()._provider_secret("gemini", str(TENANT_ID))

    assert secret == "synthetic-tenant-secret"
    resolver.assert_awaited_once_with(str(TENANT_ID), "gemini", "llm")


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _CompanyScopedConnectorSession:
    """Return a tenant-global ConnectorConfig, never another company row."""

    def __init__(self):
        self.global_config = SimpleNamespace(
            id=uuid.uuid4(),
            connector_name="hubspot",
            status="configured",
            health_status="healthy",
            auth_type="api_key",
            config={},
            credentials_encrypted={"_encrypted": "synthetic-encrypted-value"},
        )
        self.connector = SimpleNamespace(name="hubspot", status="active")
        self.company_queries: list[object] = []

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        name = getattr(entity, "__name__", str(entity))
        if name == "ConnectorConfig":
            params = statement.compile().params
            company_value = next(
                (
                    value
                    for key, value in params.items()
                    if "company_id" in key and value is not None
                ),
                None,
            )
            self.company_queries.append(company_value)
            return _Result(self.global_config if company_value is None else None)
        if name == "Company":
            return _Result(COMPANY_ID)
        if name == "Connector":
            return _Result(self.connector)
        return _Result(None)


@pytest.mark.asyncio
async def test_dispatch_readiness_falls_back_to_tenant_global_config() -> None:
    """BUG-01: a company-bound agent can use the tenant-global binding."""
    from api.v1.agents import _assert_connectors_ready_for_activation

    session = _CompanyScopedConnectorSession()
    with patch(
        "core.crypto.decrypt_for_tenant",
        return_value=json.dumps({"api_key": "synthetic-key"}),
    ):
        await _assert_connectors_ready_for_activation(
            session,
            TENANT_ID,
            ["registry-hubspot"],
            COMPANY_ID,
        )

    assert COMPANY_ID in session.company_queries
    assert None in session.company_queries


@pytest.mark.asyncio
async def test_runtime_config_resolution_uses_the_same_global_fallback() -> None:
    """BUG-01: tool config resolution must match the dispatch readiness gate."""
    from api.v1.agents import _resolve_connector_configs

    class _Context:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return False

    session = _CompanyScopedConnectorSession()
    with (
        patch("core.database.get_tenant_session", side_effect=lambda *_args, **_kwargs: _Context()),
        patch(
            "core.crypto.decrypt_for_tenant",
            return_value=json.dumps({"api_key": "synthetic-key"}),
        ),
    ):
        config, names = await _resolve_connector_configs(
            tenant_id=str(TENANT_ID),
            connector_ids=["registry-hubspot"],
            company_id=COMPANY_ID,
        )

    assert config == {"api_key": "synthetic-key"}
    assert names == ["hubspot"]


@pytest.mark.asyncio
async def test_dispatch_readiness_never_falls_back_to_another_company() -> None:
    """BUG-01 safety boundary: a different company's config remains blocked."""
    from api.v1.agents import _assert_connectors_ready_for_activation

    class _OtherCompanyOnly(_CompanyScopedConnectorSession):
        async def execute(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            name = getattr(entity, "__name__", str(entity))
            if name == "ConnectorConfig":
                params = statement.compile().params
                company_value = next(
                    (
                        value
                        for key, value in params.items()
                        if "company_id" in key and value is not None
                    ),
                    None,
                )
                self.company_queries.append(company_value)
                return _Result(
                    self.global_config
                    if company_value == OTHER_COMPANY_ID
                    else None
                )
            return await super().execute(statement)

    with pytest.raises(HTTPException) as exc_info:
        await _assert_connectors_ready_for_activation(
            _OtherCompanyOnly(),
            TENANT_ID,
            ["registry-hubspot"],
            COMPANY_ID,
        )

    assert "connector_not_ready_for_activation" in str(exc_info.value)
